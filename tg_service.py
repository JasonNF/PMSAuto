import os
import json
import random
import hmac
import hashlib
from urllib.parse import parse_qsl

from datetime import datetime, timezone, timedelta
from math import ceil

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from aiogram.types import Update, BotCommand
from bot.telegram_bot import bot, dp, build_open_keyboard
from fastapi.middleware.cors import CORSMiddleware

from emby_admin_service import emby_create_user, emby_set_password, emby_find_user_by_name, emby_enable_local_password, emby_test_login

from emby_admin_models import SessionLocal, UserAccount, RenewalCode, Settings, WatchStat, DonationStat, DailySnapshot, UserPref, Base, engine
from log import logger
from scheduler import Scheduler
import asyncio

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
WEBHOOK_PATH = "/tg/webhook"
EXTERNAL_BASE_URL = os.environ.get("EXTERNAL_BASE_URL") or ""
ADMIN_BEARER_TOKEN = os.environ.get("EMBY_ADMIN_TOKEN") or os.environ.get("ADMIN_BEARER_TOKEN") or ""
# 可提供多条可选线路（以英文逗号分隔），例如：AVAILABLE_ROUTES="a.domain.com:emby,b.domain.com:emby"
AVAILABLE_ROUTES = [s.strip() for s in (os.environ.get("AVAILABLE_ROUTES") or "").split(",") if s.strip()]

app = FastAPI(title="PMSAuto Unified Service", version="0.1.0")

# 允许本地与常见前端来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注意：StaticFiles 的挂载需在 API 路由之后进行（否则 /app/api/* 可能被静态路由拦截导致 405）

@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.on_event("startup")
def _startup_create_tables():
    """启动时自动创建缺失的数据表。"""
    try:
        Base.metadata.create_all(bind=engine)
        # 初始化默认设置：默认初始天数 180（可通过 DEFAULT_INITIAL_DAYS 环境变量覆盖）
        db = SessionLocal()
        try:
            kv = db.query(Settings).filter(Settings.key == "default_initial_days").first()
            default_days_env = os.environ.get("DEFAULT_INITIAL_DAYS")
            if not kv:
                default_days_env = os.environ.get("DEFAULT_INITIAL_DAYS")
                try:
                    init_days = int(default_days_env) if default_days_env is not None else 180
                except Exception:
                    init_days = 180
                kv = Settings(key="default_initial_days", value=str(init_days))
                db.add(kv)
                db.commit()
            else:
                # 若未设置环境变量覆盖，且当前值小于 180，则提升到 180
                if default_days_env is None:
                    try:
                        cur = int(kv.value)
                    except Exception:
                        cur = 0
                    if cur < 180:
                        kv.value = "180"
                        db.add(kv)
                        db.commit()
        finally:
            db.close()


# 管理员：测试用户名+密码是否能登录 Emby（排错用）
@app.post("/admin/user/test_login")
async def admin_user_test_login(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(400, "username/password 必填")
    try:
        ok = emby_test_login(username, password)
        if not ok:
            ok = emby_test_login(username, password, use_password_field=True)
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(500, f"测试登录异常: {e}")


# ---- Admin Settings (Bearer 保护) ----
def _check_admin_auth(request: Request):
    token = ADMIN_BEARER_TOKEN.strip()
    if not token:
        return True  # 若未配置管理员令牌，则不强制鉴权（可按需改为 False）
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    return bool(auth and auth.startswith("Bearer ") and auth.split(" ", 1)[1] == token)


@app.get("/admin/settings/default_days")
async def admin_get_default_days(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    db = SessionLocal()
    try:
        return {"default_initial_days": _get_default_initial_days(db)}
    finally:
        db.close()


@app.get("/admin/overview")
async def admin_overview(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    db = SessionLocal()
    try:
        total = db.query(UserAccount).count()
        emby_active = db.query(UserAccount).filter(UserAccount.status == "active").count()
        plex_users = 0  # 目前未集成 Plex 用户表，先占位
        return {"ok": True, "stats": {"plex": plex_users, "emby": emby_active, "total": total}}
    finally:
        db.close()


@app.post("/app/api/reset_password")
async def app_reset_password(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    new_password = (body.get("new_password") or "").strip()
    if not new_password:
        raise HTTPException(400, "new_password 必填")
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    db = SessionLocal()
    try:
        ua = _get_account_by_tg_id(db, tg_id)
        if not ua:
            raise HTTPException(404, "未绑定账户")
        # 确保本地密码策略开启，再设置密码
        try:
            emby_enable_local_password(ua.emby_user_id)
        except Exception:
            pass
        emby_set_password(ua.emby_user_id, new_password)
        return {"ok": True}
    finally:
        db.close()


# ---- Admin: 用户到期时间管理（Bearer 保护） ----
@app.post("/admin/user/extend_days")
async def admin_user_extend_days(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    emby_user_id = str(body.get("emby_user_id") or "").strip()
    days = int(body.get("days") or 0)
    if not emby_user_id:
        raise HTTPException(400, "emby_user_id 必填")
    if days == 0:
        raise HTTPException(400, "days 不能为 0")
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id == emby_user_id).first()
        if not ua:
            raise HTTPException(404, "用户不存在")
        from datetime import datetime as dt, timedelta as td
        base = ua.expires_at or dt.utcnow()
        ua.expires_at = base + td(days=days)
        db.add(ua)
        db.commit()
        return {"ok": True, "expires_at": ua.expires_at.isoformat() if ua.expires_at else None}
    finally:
        db.close()


# ---- Admin: 重置用户密码 ----
@app.post("/admin/user/reset_password")
async def admin_user_reset_password(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    emby_user_id = (body.get("emby_user_id") or "").strip()
    username = (body.get("username") or "").strip()
    new_password = (body.get("new_password") or "").strip()
    if not new_password:
        raise HTTPException(400, "new_password 必填")
    if not emby_user_id and not username:
        raise HTTPException(400, "emby_user_id 或 username 必填其一")
    # 若仅提供用户名，则查询得到 emby_user_id
    if not emby_user_id:
        info = emby_find_user_by_name(username)
        if not info or not info.get("Id"):
            raise HTTPException(404, "未找到该用户名对应的 Emby 账户")
        emby_user_id = info.get("Id")
    try:
        emby_set_password(emby_user_id, new_password)
        return {"ok": True, "emby_user_id": emby_user_id}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(500, f"重置密码失败: {e}")


@app.post("/admin/user/set_expires_by_name")
async def admin_user_set_expires_by_name(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    username = str(body.get("username") or "").strip()
    days_from_now = body.get("days_from_now")
    if not username:
        raise HTTPException(400, "username 必填")
    if days_from_now is None:
        raise HTTPException(400, "days_from_now 必填")
    try:
        days_from_now = int(days_from_now)
    except Exception:
        raise HTTPException(400, "days_from_now 必须为整数")
    if days_from_now < 0 or days_from_now > 3650:
        raise HTTPException(400, "days_from_now 应在 0~3650 之间")
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.username == username).first()
        if not ua:
            raise HTTPException(404, "用户不存在")
        from datetime import datetime as dt, timedelta as td
        ua.expires_at = (dt.utcnow() + td(days=days_from_now)) if days_from_now > 0 else None
        db.add(ua)
        db.commit()
        return {"ok": True, "emby_user_id": ua.emby_user_id, "expires_at": ua.expires_at.isoformat() if ua.expires_at else None}
    finally:
        db.close()


@app.post("/admin/user/set_expires")
async def admin_user_set_expires(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    emby_user_id = str(body.get("emby_user_id") or "").strip()
    days_from_now = body.get("days_from_now")
    if not emby_user_id:
        raise HTTPException(400, "emby_user_id 必填")
    if days_from_now is None:
        raise HTTPException(400, "days_from_now 必填")
    try:
        days_from_now = int(days_from_now)
    except Exception:
        raise HTTPException(400, "days_from_now 必须为整数")
    if days_from_now < 0 or days_from_now > 3650:
        raise HTTPException(400, "days_from_now 应在 0~3650 之间")
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id == emby_user_id).first()
        if not ua:
            raise HTTPException(404, "用户不存在")
        from datetime import datetime as dt, timedelta as td
        ua.expires_at = (dt.utcnow() + td(days=days_from_now)) if days_from_now > 0 else None
        db.add(ua)
        db.commit()
        return {"ok": True, "expires_at": ua.expires_at.isoformat() if ua.expires_at else None}
    finally:
        db.close()


@app.post("/admin/settings/default_days")
async def admin_set_default_days(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    try:
        value = int(body.get("value"))
    except Exception:
        raise HTTPException(400, "value 必须为整数")
    if value < 0 or value > 3650:
        raise HTTPException(400, "value 应在 0~3650 之间")
    db = SessionLocal()
    try:
        kv = db.query(Settings).filter(Settings.key == "default_initial_days").first()
        if not kv:
            kv = Settings(key="default_initial_days", value=str(value))
            db.add(kv)
        else:
            kv.value = str(value)
            db.add(kv)
        db.commit()
        return {"ok": True, "default_initial_days": value}
    finally:
        db.close()

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN 未配置")
    if bot is None:
        raise HTTPException(500, "Bot 未初始化")
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        update = Update.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid update: {e}")
    await dp.feed_update(bot, update)
    return PlainTextResponse("OK")

@app.get("/tg/setup")
async def tg_setup():
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN 未配置")
    if bot is None:
        raise HTTPException(500, "Bot 未初始化")
    if not EXTERNAL_BASE_URL:
        raise HTTPException(500, "EXTERNAL_BASE_URL 未配置")
    webhook_url = EXTERNAL_BASE_URL.rstrip("/") + WEBHOOK_PATH
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="开始 / 深链"),
            BotCommand(command="help", description="帮助"),
            BotCommand(command="register", description="注册/绑定"),
            BotCommand(command="points", description="查询积分"),
        ]
    )
    return {"ok": True, "webhook_url": webhook_url}


def verify_webapp_initdata(init_data: str) -> dict:
    # 解析查询字符串，提取 hash
    params = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise HTTPException(400, "Missing hash")
    # 构建 data_check_string
    data_check_arr = [f"{k}={v}" for k, v in sorted(params.items())]
    data_check_string = "\n".join(data_check_arr)
    # 计算 secret key（Telegram 文档）: secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token)
    # 注意：key 与 msg 的顺序不要颠倒
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=TELEGRAM_BOT_TOKEN.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    # 计算校验哈希
    calc_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    if calc_hash != received_hash:
        # 记录调试信息以辅助定位（不包含敏感明文，仅摘要）
        try:
            keys = sorted(list(params.keys()))
        except Exception:
            keys = []
        logger.warning(
            "WebApp initData HMAC 校验失败: keys=%s recv=%s calc=%s len=%s",
            keys,
            (received_hash or "")[:8],
            calc_hash[:8],
            len(init_data or ""),
        )
        raise HTTPException(401, "Invalid initData")
    # 解析 user 信息
    result = {"ok": True}
    user_json = params.get("user")
    if user_json:
        try:
            user = json.loads(user_json)
        except Exception:
            user = None
        if user:
            result["user"] = user
            result["tg_id"] = str(user.get("id")) if "id" in user else None
    return result


@app.post("/app/api/verify")
async def app_verify(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN 未配置")
    body = await request.json()
    init_data = body.get("initData") or ""
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    # 查询绑定信息
    info = {"bound": False, "username": None, "emby_user_id": None, "expires_at": None, "days_remaining": None, "points": 0.0, "donation": 0.0, "notify_enabled": True}
    if tg_id:
        db = SessionLocal()
        try:
            ua = db.query(UserAccount).filter(UserAccount.tg_id == tg_id).first()
            if ua:
                # 计算积分：观看时长 / 1800 秒。保留两位小数。
                pts = 0.0
                donation_amt = 0.0
                try:
                    ws = db.query(WatchStat).filter(WatchStat.emby_user_id == ua.emby_user_id).first()
                    if ws and ws.seconds_total is not None:
                        pts = round(float(ws.seconds_total) / 1800.0, 2)
                        watch_hours = round(float(ws.seconds_total) / 3600.0, 2)
                    else:
                        watch_hours = 0.0
                    ds = db.query(DonationStat).filter(DonationStat.emby_user_id == ua.emby_user_id).first()
                    if ds and ds.amount_total is not None:
                        donation_amt = float(ds.amount_total)
                        pts = round(pts + donation_amt * 2.0, 2)
                except Exception:
                    pts = pts
                    watch_hours = 0.0
                # 额外积分（正向奖励）与已消耗积分（扣减）覆盖层
                extra_pts = 0.0
                used_pts = 0.0
                try:
                    key_bonus = f"points_bonus:{ua.emby_user_id}"
                    kv_bonus = db.query(Settings).filter(Settings.key == key_bonus).first()
                    if kv_bonus and kv_bonus.value:
                        extra_pts = float(kv_bonus.value)
                    key_used = f"points_used:{ua.emby_user_id}"
                    kv_used = db.query(Settings).filter(Settings.key == key_used).first()
                    if kv_used and kv_used.value:
                        used_pts = float(kv_used.value)
                except Exception:
                    extra_pts = extra_pts
                    used_pts = used_pts

                # 通知偏好
                pref = db.query(UserPref).filter(UserPref.emby_user_id == ua.emby_user_id).first()
                notify_enabled = not bool(pref and pref.notify_opt_out)
                # 读取用户绑定线路（保存在 Settings 表，key=route:{emby_user_id}）
                try:
                    route_key = f"route:{ua.emby_user_id}"
                    kv_route = db.query(Settings).filter(Settings.key == route_key).first()
                    bound_route = (kv_route.value if kv_route and kv_route.value else None)
                except Exception:
                    bound_route = None

                # 观看等级（按累计小时数简单分级）
                def _watch_level(hours: float) -> str:
                    if hours >= 500: return "★★★★★"
                    if hours >= 200: return "★★★★"
                    if hours >= 50: return "★★★"
                    if hours >= 10: return "★★"
                    if hours > 0: return "★"
                    return "☆"

                # 标准化注册时间：提供 UTC ISO 与东八区本地日期（用于前端到天展示）
                created_at_iso = None
                created_date_cn = None
                try:
                    ca = ua.created_at
                    if ca:
                        if ca.tzinfo is None:
                            ca_utc = ca.replace(tzinfo=timezone.utc)
                        else:
                            ca_utc = ca.astimezone(timezone.utc)
                        created_at_iso = ca_utc.isoformat().replace("+00:00", "Z")
                        created_date_cn = (ca_utc + timedelta(hours=8)).strftime("%Y-%m-%d")
                except Exception:
                    pass

                info = {
                    "bound": True,
                    "username": ua.username,
                    "emby_user_id": ua.emby_user_id,
                    "created_at": ua.created_at.isoformat() if ua.created_at else None,
                    "created_at_utc": created_at_iso,
                    "created_date_local_cn": created_date_cn,
                    "expires_at": ua.expires_at.isoformat() if ua.expires_at else None,
                    "days_remaining": _compute_days_remaining(ua.expires_at),
                    "points": round(max(0.0, pts + extra_pts - used_pts), 2),
                    "watch_hours": watch_hours,
                    "watch_level": _watch_level(watch_hours),
                    "donation": donation_amt,
                    "notify_enabled": notify_enabled,
                    "entry_route": _normalize_entry_url(),
                    "bound_route": bound_route,
                    "available_routes": AVAILABLE_ROUTES,
                    "_points_base": pts,
                    "_points_bonus": extra_pts,
                    "_points_used": used_pts,
                }
        finally:
            db.close()
    return JSONResponse({"ok": True, "verify": verified, "account": info})

@app.get("/app/api/routes")
async def app_get_routes(request: Request):
    body = await request.json() if request.method == "POST" else {}
    init_data = body.get("initData") or (await request.body()).decode("utf-8") if body == {} else body.get("initData")
    verified = verify_webapp_initdata(init_data) if init_data else {"tg_id": None}
    tg_id = verified.get("tg_id")
    db = SessionLocal()
    try:
        ua = _get_account_by_tg_id(db, tg_id) if tg_id else None
        if not ua:
            return {"ok": True, "available": AVAILABLE_ROUTES, "bound": None}
        kv = db.query(Settings).filter(Settings.key == f"route:{ua.emby_user_id}").first()
        bound = kv.value if kv and kv.value else None
        return {"ok": True, "available": AVAILABLE_ROUTES, "bound": bound}
    finally:
        db.close()

@app.post("/app/api/routes/bind")
async def app_bind_route(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    route = (body.get("route") or "").strip()
    if not route:
        raise HTTPException(400, "route 必填")
    # 允许 AVAILABLE_ROUTES 使用 host|tag1,tag2 的格式，此时以 host 作为校验
    if AVAILABLE_ROUTES:
        allowed_hosts = []
        for item in AVAILABLE_ROUTES:
            host = str(item).split("|", 1)[0].strip()
            if host:
                allowed_hosts.append(host)
        if route not in allowed_hosts:
            raise HTTPException(400, "不在可选线路列表中")
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    db = SessionLocal()
    try:
        ua = _get_account_by_tg_id(db, tg_id)
        if not ua:
            raise HTTPException(404, "未绑定账户")
        key = f"route:{ua.emby_user_id}"
        kv = db.query(Settings).filter(Settings.key == key).first()
        if not kv:
            kv = Settings(key=key, value=route)
        else:
            kv.value = route
        db.add(kv)
        db.commit()
        return {"ok": True, "route": route}
    finally:
        db.close()


def _get_account_by_tg_id(db, tg_id: str):
    return db.query(UserAccount).filter(UserAccount.tg_id == tg_id).first()

def _compute_days_remaining(expires_at) -> int | None:
    if not expires_at:
        return None
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires = expires_at
    delta = expires - now
    return max(0, ceil(delta.total_seconds() / 86400))


def _get_default_initial_days(db) -> int:
    # 优先环境变量覆盖
    env_val = os.environ.get("DEFAULT_INITIAL_DAYS")
    if env_val is not None:
        try:
            v = int(env_val)
            if 0 <= v <= 3650:
                return v
        except Exception:
            pass
    # 其次读取数据库设置
    try:
        kv = db.query(Settings).filter(Settings.key == "default_initial_days").first()
        if kv and kv.value and str(kv.value).isdigit():
            return int(kv.value)
    except Exception:
        pass
    # 最后回退到 180
    return 180


def _normalize_entry_url() -> str:
    """优先使用 EMBY_ENTRY_URL；否则从 EMBY_BASE_URL 去掉末尾的 /emby 路径与多余斜杠。
    示例：
      EMBY_BASE_URL=https://emby.misaya.org/emby -> https://emby.misaya.org
    """
    entry = os.environ.get("EMBY_ENTRY_URL")
    if entry:
        return entry.rstrip("/")
    base = (os.environ.get("EMBY_BASE_URL") or "").strip()
    base = base.rstrip("/")
    if base.endswith("/emby"):
        base = base[:-5]
    return base


# ===== 幸运大转盘 =====
def _default_wheel_config():
    return {
        "min_points": 30,
        "cost_points": 5,
        "items": [
            {"label": "积分+10", "color": "#60a5fa", "weight": 1},
            {"label": "积分-10", "color": "#67e8f9", "weight": 1},
            {"label": "谢谢参与", "color": "#fca5a5", "weight": 1},
            {"label": "积分+30", "color": "#fde68a", "weight": 1},
            {"label": "Premium 7天", "color": "#e9d5ff", "weight": 1},
            {"label": "积分+50", "color": "#86efac", "weight": 1},
            {"label": "积分-200", "color": "#f87171", "weight": 1},
            {"label": "积分+75", "color": "#fde68a", "weight": 1},
        ],
    }


def _get_wheel_config(db):
    try:
        kv = db.query(Settings).filter(Settings.key == "wheel_config").first()
        if kv and kv.value:
            cfg = json.loads(kv.value)
            # 兜底
            if not isinstance(cfg.get("items", []), list) or not cfg["items"]:
                cfg["items"] = _default_wheel_config()["items"]
            return cfg
    except Exception:
        pass
    return _default_wheel_config()


def _set_wheel_config(db, cfg: dict):
    if not isinstance(cfg, dict):
        raise HTTPException(400, "配置应为 JSON 对象")
    items = cfg.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items 必须为非空数组")
    # 简单校验字段
    for it in items:
        if not isinstance(it, dict) or "label" not in it:
            raise HTTPException(400, "items 中每项需包含 label")
        it.setdefault("color", "#93c5fd")
        it.setdefault("weight", 1)
    min_points = int(cfg.get("min_points", 0))
    cost_points = int(cfg.get("cost_points", 0))
    cfg = {"min_points": min_points, "cost_points": cost_points, "items": items}
    kv = db.query(Settings).filter(Settings.key == "wheel_config").first()
    if not kv:
        kv = Settings(key="wheel_config", value=json.dumps(cfg, ensure_ascii=False))
    else:
        kv.value = json.dumps(cfg, ensure_ascii=False)
    db.add(kv)
    db.commit()
    return cfg


@app.get("/app/api/wheel/config")
async def get_wheel_config():
    db = SessionLocal()
    try:
        return _get_wheel_config(db)
    finally:
        db.close()


@app.post("/admin/wheel/config")
async def set_wheel_config(request: Request):
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    db = SessionLocal()
    try:
        cfg = _set_wheel_config(db, body)
        return {"ok": True, "config": cfg}
    finally:
        db.close()


@app.post("/app/api/wheel/spin")
async def wheel_spin(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    init_data = (body.get("initData") or "").strip()
    verified = verify_webapp_initdata(init_data) if init_data else {"tg_id": None}
    tg_id = verified.get("tg_id")
    db = SessionLocal()
    try:
        cfg = _get_wheel_config(db)
        items = cfg.get("items", [])
        n = len(items) or 8

        # 找到用户并计算当前有效积分
        ua = _get_account_by_tg_id(db, tg_id) if tg_id else None
        if not ua:
            raise HTTPException(401, "未绑定账户")
        # 基础积分
        pts_base = 0.0
        donation_amt = 0.0
        try:
            ws = db.query(WatchStat).filter(WatchStat.emby_user_id == ua.emby_user_id).first()
            if ws and ws.seconds_total is not None:
                pts_base = round(float(ws.seconds_total) / 1800.0, 2)
            ds = db.query(DonationStat).filter(DonationStat.emby_user_id == ua.emby_user_id).first()
            if ds and ds.amount_total is not None:
                donation_amt = float(ds.amount_total)
                pts_base = round(pts_base + donation_amt * 2.0, 2)
        except Exception:
            pts_base = pts_base
        # bonus/used 覆盖层
        key_bonus = f"points_bonus:{ua.emby_user_id}"
        kv_bonus = db.query(Settings).filter(Settings.key == key_bonus).first()
        extra_pts = float(kv_bonus.value) if kv_bonus and kv_bonus.value else 0.0
        key_used = f"points_used:{ua.emby_user_id}"
        kv_used = db.query(Settings).filter(Settings.key == key_used).first()
        used_pts = float(kv_used.value) if kv_used and kv_used.value else 0.0
        current_points = round(max(0.0, pts_base + extra_pts - used_pts), 2)

        min_points = int(cfg.get("min_points", 0))
        cost_points = int(cfg.get("cost_points", 0))
        if current_points < min_points:
            return {"ok": False, "reason": "POINTS_TOO_LOW", "need": min_points, "have": current_points}
        if current_points < cost_points:
            return {"ok": False, "reason": "POINTS_NOT_ENOUGH", "need": cost_points, "have": current_points}

        # 先扣参与消耗（记入 used）
        used_pts_new = round(used_pts + cost_points, 2)
        if kv_used is None:
            kv_used = Settings(key=key_used, value=str(used_pts_new))
        else:
            kv_used.value = str(used_pts_new)
        db.add(kv_used)
        db.commit()

        # 随机抽取
        weights = [max(0.0, float(it.get("weight", 1))) for it in items] if items else None
        if items and sum(weights) > 0:
            rnd = random.random() * sum(weights)
            acc = 0.0
            pick = 0
            for i, w in enumerate(weights):
                acc += w
                if rnd <= acc:
                    pick = i
                    break
        else:
            pick = random.randint(0, n-1)
        prize_label = items[pick]["label"] if items and 0 <= pick < len(items) else f"ITEM {pick+1}"

        # 应用奖品
        applied = {"bonus": 0.0, "used": 0.0, "days": 0}
        try:
            import re
            m = re.search(r"([+-]?)(\d+)", prize_label)
            if "积分" in prize_label and m:
                sign = m.group(1) or "+"
                val = int(m.group(2))
                if sign == "-":
                    # 负向：记入 used
                    used_pts_new = round(used_pts_new + val, 2)
                    kv_used.value = str(used_pts_new)
                    db.add(kv_used)
                    applied["used"] = val
                else:
                    # 正向：记入 bonus
                    extra_pts_new = round(extra_pts + val, 2)
                    if kv_bonus is None:
                        kv_bonus = Settings(key=key_bonus, value=str(extra_pts_new))
                    else:
                        kv_bonus.value = str(extra_pts_new)
                    db.add(kv_bonus)
                    applied["bonus"] = val
                db.commit()
            elif "Premium" in prize_label or "天" in prize_label:
                # 延期天数，匹配数字
                import re
                m2 = re.search(r"(\d+)", prize_label)
                days = int(m2.group(1)) if m2 else 0
                if days > 0:
                    from datetime import datetime as dt, timedelta as td
                    base = ua.expires_at or dt.utcnow()
                    ua.expires_at = base + td(days=days)
                    db.add(ua); db.commit()
                    applied["days"] = days
        except Exception:
            pass

        # 最新积分
        kv_bonus = db.query(Settings).filter(Settings.key == key_bonus).first()
        extra_pts = float(kv_bonus.value) if kv_bonus and kv_bonus.value else extra_pts
        kv_used = db.query(Settings).filter(Settings.key == key_used).first()
        used_pts = float(kv_used.value) if kv_used and kv_used.value else used_pts_new
        new_points = round(max(0.0, pts_base + extra_pts - used_pts), 2)

        return {"ok": True, "index": pick, "prize": prize_label, "points": new_points, "applied": applied}
    finally:
        db.close()


def _local_datestr(dt_obj: datetime | None = None) -> str:
    dt_obj = dt_obj or datetime.now()
    return dt_obj.strftime("%Y-%m-%d")


async def _send_daily_points_to_all():
    if bot is None:
        return
    db = SessionLocal()
    try:
        # 取所有已绑定且激活的用户
        users = db.query(UserAccount).filter(UserAccount.tg_id != None, UserAccount.status == "active").all()
        today = datetime.now()
        yday = today - timedelta(days=1)
        d_today = _local_datestr(today)
        d_yday = _local_datestr(yday)
        for ua in users:
            # 当前总计
            ws = db.query(WatchStat).filter(WatchStat.emby_user_id == ua.emby_user_id).first()
            ds = db.query(DonationStat).filter(DonationStat.emby_user_id == ua.emby_user_id).first()
            sec_total = int(ws.seconds_total) if ws and ws.seconds_total is not None else 0
            don_total = int(ds.amount_total) if ds and ds.amount_total is not None else 0

            # 今日快照（存储当前总计）
            snap_today = (
                db.query(DailySnapshot)
                .filter(DailySnapshot.emby_user_id == ua.emby_user_id, DailySnapshot.date == d_today)
                .first()
            )
            if not snap_today:
                snap_today = DailySnapshot(
                    emby_user_id=ua.emby_user_id,
                    date=d_today,
                    seconds_total=sec_total,
                    donation_total=don_total,
                )
            else:
                snap_today.seconds_total = sec_total
                snap_today.donation_total = don_total
            db.add(snap_today)

            # 昨日快照（用于计算增量），不存在则按0处理
            snap_yday = (
                db.query(DailySnapshot)
                .filter(DailySnapshot.emby_user_id == ua.emby_user_id, DailySnapshot.date == d_yday)
                .first()
            )
            y_sec = int(snap_yday.seconds_total) if snap_yday and snap_yday.seconds_total is not None else 0
            y_don = int(snap_yday.donation_total) if snap_yday and snap_yday.donation_total is not None else 0

            # 计算增量
            delta_sec = max(0, sec_total - y_sec)
            delta_don = max(0, don_total - y_don)
            delta_hours = round(delta_sec / 3600.0, 2)
            delta_pts_watch = round(delta_sec / 1800.0, 2)
            delta_pts_don = round(delta_don * 2.0, 2)
            delta_pts = round(delta_pts_watch + delta_pts_don, 2)

            # 通知开关：若用户关闭或无任何增量，则跳过
            pref = db.query(UserPref).filter(UserPref.emby_user_id == ua.emby_user_id).first()
            if (pref and pref.notify_opt_out) or (delta_sec == 0 and delta_don == 0):
                continue

            # 当前总积分/总时长
            total_pts = round(sec_total / 1800.0 + don_total * 2.0, 2)
            total_hours = round(sec_total / 3600.0, 2)

            # 组装消息（贴近示例样式）
            lines = [
                "Emby 观看积分更新通知",
                "====================",
                "",
                f"新增观看时长：{delta_hours} 小时",
                f"新增观看积分：{delta_pts_watch}",
                f"捐赠积分增加：{delta_pts_don}",
                f"Premium 流量使用情况：0.0 GB",
                f"超出每日流量限额：0 GB",
                f"流量消耗积分：0",
                "",
                f"积分变化：{delta_pts}",
                "",
                "--------------------",
                "",
                f"当前总积分：{total_pts}",
                f"当前总观看时长：{total_hours} 小时",
                "",
                "====================",
            ]
            text = "\n".join(lines)

            # 发送带 Open 按钮的消息
            from bot.telegram_bot import WEBAPP_URL
            url = WEBAPP_URL + "#home"
            kb = build_open_keyboard(label="Open", url=url)
            try:
                await bot.send_message(chat_id=ua.tg_id, text=text, reply_markup=kb)
            except Exception as e:
                logger.warning("发送每日通知失败 tg_id=%s: %s", ua.tg_id, e)

        db.commit()
    finally:
        db.close()


def _cron_daily_points_notify():
    """在调度线程中触发 async 发送逻辑。"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        fut = asyncio.run_coroutine_threadsafe(_send_daily_points_to_all(), loop)
        fut.result(timeout=60)
    except Exception as e:
        logger.warning("触发每日通知失败: %s", e)


@app.post("/admin/notify")
async def admin_notify(request: Request):
    """向指定 tg_id 发送一条包含 WebApp “Open” 按钮的通知消息。
    受 Bearer 鉴权保护。参数：
    - tg_id: Telegram 用户ID（字符串）
    - text: 通知文案，例如 "Plex 观看积分更新通知"
    - suffix: 可选，形如 "#home" 或 "#admin" 或 "?from=notify"，会拼接到 WEBAPP_URL 后
    """
    if not _check_admin_auth(request):
        raise HTTPException(401, "Unauthorized")
    if bot is None:
        raise HTTPException(500, "Bot 未初始化")
    body = await request.json()
    tg_id = str(body.get("tg_id") or "").strip()
    text = (body.get("text") or "").strip() or "打开 MiniApp"
    suffix = (body.get("suffix") or "").strip()
    if not tg_id:
        raise HTTPException(400, "tg_id 必填")
    # 组装 URL：如果 suffix 以 # 或 ? 开头则拼接至 MiniApp URL，由端内前端处理
    from bot.telegram_bot import WEBAPP_URL
    url = WEBAPP_URL + suffix if (suffix.startswith("#") or suffix.startswith("?")) else WEBAPP_URL
    kb = build_open_keyboard(label="Open", url=url)
    try:
        await bot.send_message(chat_id=tg_id, text=text, reply_markup=kb)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"发送失败: {e}")


@app.get("/app/api/notify_pref")
async def app_get_notify_pref(request: Request):
    body = await request.json() if request.method == "POST" else {}
    init_data = body.get("initData") or (await request.body()).decode("utf-8") if body == {} else body.get("initData")
    # 兼容 GET 无 body 的情况，不强制
    verified = verify_webapp_initdata(init_data) if init_data else {"tg_id": None}
    tg_id = verified.get("tg_id")
    db = SessionLocal()
    try:
        ua = _get_account_by_tg_id(db, tg_id) if tg_id else None
        if not ua:
            return {"ok": True, "enabled": True}
        pref = db.query(UserPref).filter(UserPref.emby_user_id == ua.emby_user_id).first()
        enabled = not bool(pref and pref.notify_opt_out)
        return {"ok": True, "enabled": enabled}
    finally:
        db.close()


@app.post("/app/api/notify_pref")
async def app_set_notify_pref(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    enabled = bool(body.get("enabled", True))
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    db = SessionLocal()
    try:
        ua = _get_account_by_tg_id(db, tg_id)
        if not ua:
            raise HTTPException(404, "未绑定账户")
        pref = db.query(UserPref).filter(UserPref.emby_user_id == ua.emby_user_id).first()
        if not pref:
            pref = UserPref(emby_user_id=ua.emby_user_id, notify_opt_out=not enabled)
        else:
            pref.notify_opt_out = not enabled
        db.add(pref)
        db.commit()
        return {"ok": True, "enabled": enabled}
    finally:
        db.close()


@app.post("/app/api/register")
async def app_register(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN 未配置")
    body = await request.json()
    init_data = body.get("initData") or ""
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    # 不再让前端决定初始天数，由服务端设置
    if not username or not password:
        raise HTTPException(400, "username/password 必填")
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    emby_user = emby_create_user(username)
    user_id = emby_user.get("Id")
    if not user_id:
        raise HTTPException(500, "Emby 响应缺少 Id")
    # 确保启用本地密码策略后再设置密码，避免出现“空密码可登录”的情况
    try:
        emby_enable_local_password(user_id)
    except Exception:
        pass
    emby_set_password(user_id, password)
    # 立即校验密码是否可用，避免出现“实际空密码可登录”的情况
    try:
        ok = emby_test_login(username, password)
        if not ok:
            ok = emby_test_login(username, password, use_password_field=True)
        if not ok:
            raise HTTPException(500, "密码设置后校验未通过，请稍后重试或联系管理员")
    except HTTPException:
        raise
    except Exception:
        # 校验异常时，不阻断，但建议记录
        logger.warning("register password verify skipped due to exception")
    from datetime import timedelta, datetime as dt
    db = SessionLocal()
    try:
        default_days = _get_default_initial_days(db)
        ua = UserAccount(
            emby_user_id=user_id,
            username=username,
            tg_id=str(tg_id),
            status="active",
            expires_at=(dt.utcnow() + timedelta(days=default_days)) if default_days > 0 else None,
        )
        db.add(ua)
        db.commit()
        return JSONResponse({"ok": True, "emby_user_id": user_id, "username": username})
    finally:
        db.close()


@app.post("/app/api/bind_by_name")
async def app_bind_by_name(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    username = (body.get("username") or "").strip()
    expires_days = body.get("expires_days")
    if not username:
        raise HTTPException(400, "username 必填")
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    # 查 Emby 是否存在该用户名
    info = emby_find_user_by_name(username)
    if not info:
        raise HTTPException(404, "User not found")
    emby_user_id = info.get("Id")
    if not emby_user_id:
        raise HTTPException(500, "Emby 响应缺少用户Id")
    # 绑定到本地账户（若不存在则创建一条本地记录）
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id == emby_user_id).first()
        if not ua:
            ua = UserAccount(
                emby_user_id=emby_user_id,
                username=username,
                status="active",
            )
        ua.tg_id = str(tg_id)
        # 如果还没有到期时间，按默认初始天数设置
        if ua.expires_at is None:
            from datetime import datetime as dt, timedelta as td
            default_days = _get_default_initial_days(db)
            ua.expires_at = (dt.utcnow() + td(days=default_days)) if default_days > 0 else None
        db.add(ua)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/app/api/bind")
async def app_bind(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    emby_user_id = (body.get("emby_user_id") or "").strip()
    expires_days = body.get("expires_days")
    if not emby_user_id:
        raise HTTPException(400, "emby_user_id 必填")
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id == emby_user_id).first()
        if not ua:
            raise HTTPException(404, "用户不存在")
        ua.tg_id = str(tg_id)
        if ua.expires_at is None:
            from datetime import datetime as dt, timedelta as td
            default_days = _get_default_initial_days(db)
            ua.expires_at = (dt.utcnow() + td(days=default_days)) if default_days > 0 else None
        db.add(ua)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.post("/app/api/redeem")
async def app_redeem(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    emby_user_id = (body.get("emby_user_id") or "").strip()
    code = (body.get("code") or "").strip()
    if not emby_user_id or not code:
        raise HTTPException(400, "emby_user_id 与 code 必填")
    verified = verify_webapp_initdata(init_data)
    tg_id = verified.get("tg_id")
    if not tg_id:
        raise HTTPException(400, "未获取到 Telegram 用户")
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id == emby_user_id).first()
        if not ua:
            raise HTTPException(404, "用户不存在")
        rc = db.query(RenewalCode).filter(RenewalCode.code == code).first()
        if not rc:
            raise HTTPException(404, "Code 不存在")
        if rc.redeemed_by:
            raise HTTPException(400, "Code 已被兑换")
        from datetime import datetime as dt, timedelta as td
        if rc.expired_at and rc.expired_at < dt.utcnow():
            raise HTTPException(400, "Code 已过期")
        base = ua.expires_at or dt.utcnow()
        ua.expires_at = base + td(days=rc.days)
        rc.redeemed_by = emby_user_id
        rc.redeemed_at = dt.utcnow()
        db.add_all([ua, rc])
        db.commit()
        return {"ok": True, "expires_at": ua.expires_at.isoformat() if ua.expires_at else None, "days_remaining": _compute_days_remaining(ua.expires_at)}
    finally:
        db.close()

# 最后再挂载 MiniApp 静态目录，避免静态路由拦截 /app/api/* 导致 405
app.mount("/app", StaticFiles(directory="webapp", html=True), name="webapp")


@app.get("/tg/me")
async def tg_me():
    if bot is None:
        raise HTTPException(500, "Bot 未初始化")
    me = await bot.get_me()
    return {
        "ok": True,
        "id": me.id,
        "username": me.username,
        "first_name": me.first_name,
        "EXTERNAL_BASE_URL": EXTERNAL_BASE_URL,
    }
