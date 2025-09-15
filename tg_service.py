import os
import json
import hmac
import hashlib
from urllib.parse import parse_qsl

from datetime import datetime, timezone, timedelta
from math import ceil

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from aiogram.types import Update, BotCommand
from bot.telegram_bot import bot, dp
from fastapi.middleware.cors import CORSMiddleware

from emby_admin_service import emby_create_user, emby_set_password

from emby_admin_models import SessionLocal, UserAccount, RenewalCode, Base, engine
from log import logger

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
WEBHOOK_PATH = "/tg/webhook"
EXTERNAL_BASE_URL = os.environ.get("EXTERNAL_BASE_URL") or ""

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
        logger.info("数据库表检查/创建完成")
    except Exception as e:
        # 不阻断启动，但打印错误便于排查
        logger.error("数据库表创建失败: %s", e)

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
    info = {"bound": False, "username": None, "emby_user_id": None, "expires_at": None, "days_remaining": None}
    if tg_id:
        db = SessionLocal()
        try:
            ua = db.query(UserAccount).filter(UserAccount.tg_id == tg_id).first()
            if ua:
                info = {
                    "bound": True,
                    "username": ua.username,
                    "emby_user_id": ua.emby_user_id,
                    "expires_at": ua.expires_at.isoformat() if ua.expires_at else None,
                    "days_remaining": _compute_days_remaining(ua.expires_at),
                }
        finally:
            db.close()
    return JSONResponse({"ok": True, "verify": verified, "account": info})


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


@app.post("/app/api/register")
async def app_register(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN 未配置")
    body = await request.json()
    init_data = body.get("initData") or ""
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    expires_days = body.get("expires_days")
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
    emby_set_password(user_id, password)
    from datetime import timedelta, datetime as dt
    db = SessionLocal()
    try:
        ua = UserAccount(emby_user_id=user_id, username=username, tg_id=str(tg_id), status="active",
                        expires_at=(dt.utcnow() + timedelta(days=int(expires_days))) if expires_days else None)
        db.add(ua)
        db.commit()
        return JSONResponse({"ok": True, "emby_user_id": user_id, "username": username})
    finally:
        db.close()


@app.post("/app/api/bind")
async def app_bind(request: Request):
    body = await request.json()
    init_data = body.get("initData") or ""
    emby_user_id = (body.get("emby_user_id") or "").strip()
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
