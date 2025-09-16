from __future__ import annotations
from datetime import datetime, timedelta
import secrets
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from emby_admin_models import SessionLocal, init_db, UserAccount, RenewalCode, AuditLog, Settings, DonationStat, WatchStat
from log import logger
from settings import EMBY_BASE_URL, EMBY_API_TOKEN, ADMIN_BEARER_TOKEN
from scheduler import Scheduler

app = FastAPI(title="PMSAuto Emby Admin", version="0.1.0")

# ---- Auth middleware ----
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    import os
    env_token = (os.environ.get("EMBY_ADMIN_TOKEN") or "").strip()
    cfg_token = (ADMIN_BEARER_TOKEN or "").strip()
    token = env_token or cfg_token
    if token:
        auth = request.headers.get("authorization")
        if not auth or not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != token:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

# ----- Emby API helpers -----
# 同时通过 Header 与 Query 传递令牌，兼容不同 Emby 反代/版本要求
HEADERS = {
    "Content-Type": "application/json",
    "X-Emby-Token": EMBY_API_TOKEN,
}


def emby_create_user(username: str) -> dict:
    # 若已存在同名用户，先报错，避免 Emby 返回不直观的 400
    exists = emby_find_user_by_name(username)
    if exists:
        raise HTTPException(status_code=400, detail=f"User already exists: {username}")
    url = f"{EMBY_BASE_URL}/Users/New"
    resp = requests.post(
        url,
        headers=HEADERS,
        params={"api_key": EMBY_API_TOKEN},
        json={"Name": username},
    )
    if resp.status_code >= 300:
        txt = resp.text
        logger.error(txt)
        raise HTTPException(status_code=resp.status_code, detail=f"Create user failed: {txt}")
    return resp.json()


def emby_set_password(user_id: str, new_password: str) -> None:
    # Emby/Jellyfin: POST /Users/{id}/Password
    # 兼容不同版本的字段命名，首次尝试 NewPw/CurrentPw，若未生效再尝试 NewPassword/CurrentPassword
    url = f"{EMBY_BASE_URL}/Users/{user_id}/Password"
    def _post_pwd(payload: dict):
        return requests.post(url, headers=HEADERS, params={"api_key": EMBY_API_TOKEN}, json=payload)

    # 第一次尝试
    resp = _post_pwd({"ResetPassword": True, "NewPw": new_password, "CurrentPw": ""})
    if resp.status_code >= 300:
        txt = resp.text
        logger.error("set_password(NewPw) failed: %s", txt)
        raise HTTPException(status_code=resp.status_code, detail=f"Set password failed: {txt}")

    # 验证是否已设置成功
    ok = False
    try:
        u = emby_get_user(user_id) or {}
        # 兼容不同字段
        if u.get("HasPassword") is True or u.get("HasConfiguredPassword") is True:
            ok = True
    except Exception:
        pass

    if not ok:
        # 再次尝试另一套字段
        resp2 = _post_pwd({"ResetPassword": True, "NewPassword": new_password, "CurrentPassword": ""})
        if resp2.status_code >= 300:
            txt = resp2.text
            logger.error("set_password(NewPassword) failed: %s", txt)
            raise HTTPException(status_code=resp2.status_code, detail=f"Set password failed: {txt}")
        try:
            u2 = emby_get_user(user_id) or {}
            if u2.get("HasPassword") is True or u2.get("HasConfiguredPassword") is True:
                ok = True
        except Exception:
            ok = True  # 若无法校验，假定成功，避免阻断
    # 登录校验（尽最大努力，不记录密码）
    try:
        name = None
        uinfo = emby_get_user(user_id)
        if uinfo:
            name = uinfo.get("Name")
        if name:
            if not emby_test_login(name, new_password):
                # 某些版本要求使用 Password 字段登录，再试一遍
                if not emby_test_login(name, new_password, use_password_field=True):
                    logger.error("Password verification failed for user %s", user_id)
                    raise HTTPException(status_code=500, detail="密码设置后校验未通过，请检查 Emby 配置")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("skip login verify due to error: %s", e)


def emby_set_disabled(user_id: str, is_disabled: bool) -> None:
    # Emby: POST /Users/{id}/Policy with body containing "IsDisabled"
    url = f"{EMBY_BASE_URL}/Users/{user_id}/Policy"
    payload = {"IsDisabled": is_disabled}
    resp = requests.post(url, headers=HEADERS, params={"api_key": EMBY_API_TOKEN}, json=payload)
    if resp.status_code >= 300:
        txt = resp.text
        logger.error(txt)
        raise HTTPException(status_code=resp.status_code, detail=f"Update policy failed: {txt}")


def emby_test_login(username: str, password: str, *, use_password_field: bool=False) -> bool:
    """尝试使用用户名+密码在 Emby 进行一次认证，返回是否成功。
    - 首选 POST /Users/AuthenticateByName
    - 添加 X-Emby-Authorization 以兼容 Emby 的鉴权要求
    - 支持 {Username,Pw} 与 {Username,Password}
    """
    try:
        url = f"{EMBY_BASE_URL}/Users/AuthenticateByName"
        body = {"Username": username}
        if use_password_field:
            body["Password"] = password
        else:
            body["Pw"] = password
        headers = {
            "Content-Type": "application/json",
            # 一些 Emby 版本需要此头才允许用户名密码认证
            "X-Emby-Authorization": 'MediaBrowser Client="PMSAuto", Device="Server", DeviceId="pmsauto", Version="1.0"',
        }
        resp = requests.post(url, headers=headers, json=body)
        if resp.status_code == 200:
            return True
        else:
            logger.info("emby_test_login non-200: %s", resp.status_code)
            return False
    except Exception as e:
        logger.warning("emby_test_login error: %s", e)
        return False


def emby_enable_local_password(user_id: str) -> None:
    """确保用户策略启用了本地密码登录（EnableUserLocalPassword）。
    注意：某些 Emby/Jellyfin 部署默认关闭本地密码，需要显式打开。
    """
    url = f"{EMBY_BASE_URL}/Users/{user_id}/Policy"
    payload = {"EnableUserLocalPassword": True, "IsDisabled": False}
    resp = requests.post(url, headers=HEADERS, params={"api_key": EMBY_API_TOKEN}, json=payload)
    if resp.status_code >= 300:
        txt = resp.text
        logger.error("enable_local_password failed: %s", txt)
        raise HTTPException(status_code=resp.status_code, detail=f"Enable local password failed: {txt}")


def emby_find_user_by_name(username: str) -> dict | None:
    """尝试通过 Emby Users 列表查找同名用户，避免重复创建。
    说明：不同 Emby 版本的搜索参数可能存在差异，这里使用全量列表做一次本地精确匹配，兼容性最好。
    """
    try:
        url = f"{EMBY_BASE_URL}/Users"
        resp = requests.get(url, headers=HEADERS, params={"api_key": EMBY_API_TOKEN})
        if resp.status_code >= 300:
            logger.warning("List users failed: %s", resp.text)
            return None
        items = resp.json() or []
        for it in items:
            try:
                if (it.get("Name") or "").strip().lower() == (username or "").strip().lower():
                    return it
            except Exception:
                continue
        return None
    except Exception as e:
        logger.warning("find_user_by_name error: %s", e)
        return None


def emby_get_user(user_id: str) -> dict | None:
    """获取指定 Emby 用户，成功返回 JSON，否则返回 None。"""
    try:
        url = f"{EMBY_BASE_URL}/Users/{user_id}"
        resp = requests.get(url, headers=HEADERS, params={"api_key": EMBY_API_TOKEN})
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Get user %s failed: %s %s", user_id, resp.status_code, resp.text)
        return None
    except Exception as e:
        logger.warning("emby_get_user error: %s", e)
        return None

# ----- Schemas -----
class RegisterReq(BaseModel):
    username: str
    password: str
    tg_id: Optional[str] = None
    expires_days: Optional[int] = None

class ResetPasswordReq(BaseModel):
    new_password: str

class BindTGReq(BaseModel):
    tg_id: str

class CreateCodeReq(BaseModel):
    days: int
    expired_at: Optional[datetime] = None

class RedeemCodeReq(BaseModel):
    code: str

class WatchSetReq(BaseModel):
    emby_user_id: str
    seconds: int  # 非负整数

class WatchAddReq(BaseModel):
    emby_user_id: str
    delta: int  # 可正可负

# ----- Background tasks -----

def expire_overdue_users():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        items = db.query(UserAccount).filter(
            UserAccount.expires_at != None,
            UserAccount.expires_at < now,
            UserAccount.status != "archived",
        ).all()
        for ua in items:
            try:
                emby_set_disabled(ua.emby_user_id, True)
                ua.status = "archived"
                db.add(ua)
                db.add(
                    AuditLog(
                        action="auto_archive",
                        target=ua.emby_user_id,
                        detail=f"expired at {ua.expires_at}",
                    )
                )
                logger.info(f"Auto archived user {ua.username} ({ua.emby_user_id})")
            except Exception as e:
                logger.error(f"Auto archive failed for {ua.emby_user_id}: {e}")
        db.commit()
    finally:
        db.close()


# ----- Admin settings (default initial days) -----
class SetDefaultDaysReq(BaseModel):
    value: int

@app.get("/admin/settings/default_days")
def get_default_days():
    db = SessionLocal()
    try:
        kv = db.query(Settings).filter(Settings.key == "default_initial_days").first()
        v = 30
        if kv and kv.value and str(kv.value).isdigit():
            v = int(kv.value)
        return {"default_initial_days": v}
    finally:
        db.close()

@app.post("/admin/settings/default_days")
def set_default_days(req: SetDefaultDaysReq):
    if req.value < 0 or req.value > 3650:
        raise HTTPException(400, "value should be between 0 and 3650")
    db = SessionLocal()
    try:
        kv = db.query(Settings).filter(Settings.key == "default_initial_days").first()
        if not kv:
            kv = Settings(key="default_initial_days", value=str(req.value))
            db.add(kv)
        else:
            kv.value = str(req.value)
            db.add(kv)
        db.add(AuditLog(action="set_default_days", detail=f"value={req.value}"))
        db.commit()
        return {"ok": True, "default_initial_days": req.value}
    finally:
        db.close()

# ----- Watch (seconds) Admin (manual) -----
@app.get("/admin/watch/get")
def watch_get(emby_user_id: str):
    db = SessionLocal()
    try:
        ws = db.query(WatchStat).filter(WatchStat.emby_user_id == emby_user_id).first()
        sec = int(ws.seconds_total) if ws and ws.seconds_total is not None else 0
        return {"emby_user_id": emby_user_id, "seconds": sec}
    finally:
        db.close()

@app.post("/admin/watch/set")
def watch_set(req: WatchSetReq):
    if not req.emby_user_id:
        raise HTTPException(400, "emby_user_id 必填")
    if not isinstance(req.seconds, int) or req.seconds < 0:
        raise HTTPException(400, "seconds 应为非负整数")
    db = SessionLocal()
    try:
        ws = db.query(WatchStat).filter(WatchStat.emby_user_id == req.emby_user_id).first()
        if not ws:
            ws = WatchStat(emby_user_id=req.emby_user_id, seconds_total=req.seconds)
        else:
            ws.seconds_total = req.seconds
        db.add(ws)
        db.add(AuditLog(action="watch_set", target=req.emby_user_id, detail=f"seconds={req.seconds}"))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/admin/watch/add")
def watch_add(req: WatchAddReq):
    if not req.emby_user_id:
        raise HTTPException(400, "emby_user_id 必填")
    if not isinstance(req.delta, int):
        raise HTTPException(400, "delta 应为整数")
    db = SessionLocal()
    try:
        ws = db.query(WatchStat).filter(WatchStat.emby_user_id == req.emby_user_id).first()
        if not ws:
            ws = WatchStat(emby_user_id=req.emby_user_id, seconds_total=0)
        ws.seconds_total = int(ws.seconds_total or 0) + req.delta
        if ws.seconds_total < 0:
            ws.seconds_total = 0
        db.add(ws)
        db.add(AuditLog(action="watch_add", target=req.emby_user_id, detail=f"delta={req.delta}"))
        db.commit()
        return {"ok": True, "seconds": int(ws.seconds_total)}
    finally:
        db.close()

# ----- Routes -----
@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Emby Admin service started")
    # 启用自动过期封存任务
    try:
        scheduler = Scheduler()
        scheduler.add_job(
            expire_overdue_users,
            trigger="interval",
            minutes=60,
            id="auto_expire_archive",
            replace_existing=True,
        )
        logger.info("Auto-expire scheduler started (60m)")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

@app.post("/api/users/register")
def register(req: RegisterReq):
    db = SessionLocal()
    try:
        # create on Emby
        emby_user = emby_create_user(req.username)
        user_id = emby_user.get("Id")
        if not user_id:
            raise HTTPException(500, "Emby response missing Id")
        # 确保本地密码策略开启，再设置密码
        try:
            emby_enable_local_password(user_id)
        except Exception as e:
            logger.warning("enable_local_password warn: %s", e)
        emby_set_password(user_id, req.password)
        # save local
        ua = UserAccount(
            emby_user_id=user_id,
            username=req.username,
            tg_id=req.tg_id,
            status="active",
            expires_at=(datetime.utcnow() + timedelta(days=req.expires_days)) if req.expires_days else None,
        )
        db.add(ua)
        db.commit()
        db.refresh(ua)
        db.add(AuditLog(action="register", target=user_id, detail=f"username={req.username}"))
        db.commit()
        return {"emby_user_id": user_id, "username": req.username}
    finally:
        db.close()

@app.post("/api/users/{emby_user_id}/reset_password")
def reset_password(emby_user_id: str, req: ResetPasswordReq):
    db = SessionLocal()
    try:
        emby_set_password(emby_user_id, req.new_password)
        db.add(AuditLog(action="reset_password", target=emby_user_id))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/api/users/{emby_user_id}/bind_tg")
@app.post("/api/users/{emby_user_id}/rebind_tg")
def bind_tg(emby_user_id: str, req: BindTGReq):
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id==emby_user_id).first()
        if not ua:
            raise HTTPException(404, "User not found")
        ua.tg_id = req.tg_id
        db.add(ua)
        db.add(AuditLog(action="bind_tg", target=emby_user_id, detail=f"tg_id={req.tg_id}"))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/api/users/{emby_user_id}/archive")
def archive_user(emby_user_id: str):
    db = SessionLocal()
    try:
        emby_set_disabled(emby_user_id, True)
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id==emby_user_id).first()
        if ua:
            ua.status = "archived"
            db.add(ua)
        db.add(AuditLog(action="archive", target=emby_user_id))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/api/users/{emby_user_id}/unarchive")
def unarchive_user(emby_user_id: str):
    db = SessionLocal()
    try:
        emby_set_disabled(emby_user_id, False)
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id==emby_user_id).first()
        if ua:
            ua.status = "active"
            db.add(ua)
        db.add(AuditLog(action="unarchive", target=emby_user_id))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/api/renewal_codes/create")
def create_code(req: CreateCodeReq):
    db = SessionLocal()
    try:
        code = secrets.token_urlsafe(12)
        rc = RenewalCode(code=code, days=req.days, expired_at=req.expired_at)
        db.add(rc)
        db.add(AuditLog(action="create_code", target=code, detail=f"days={req.days}"))
        db.commit()
        return {"code": code}
    finally:
        db.close()

@app.post("/api/users/{emby_user_id}/redeem")
def redeem_code(emby_user_id: str, req: RedeemCodeReq):
    db = SessionLocal()
    try:
        ua = db.query(UserAccount).filter(UserAccount.emby_user_id==emby_user_id).first()
        if not ua:
            raise HTTPException(404, "User not found")
        rc = db.query(RenewalCode).filter(RenewalCode.code==req.code).first()
        if not rc:
            raise HTTPException(404, "Code not found")
        if rc.redeemed_by:
            raise HTTPException(400, "Code already redeemed")
        if rc.expired_at and rc.expired_at < datetime.utcnow():
            raise HTTPException(400, "Code expired")
        # extend
        base = ua.expires_at or datetime.utcnow()
        ua.expires_at = base + timedelta(days=rc.days)
        rc.redeemed_by = emby_user_id
        rc.redeemed_at = datetime.utcnow()
        db.add_all([ua, rc])
        db.add(AuditLog(action="redeem_code", target=emby_user_id, detail=f"code={rc.code}, +{rc.days}d"))
        db.commit()
        return {"expires_at": ua.expires_at}
    finally:
        db.close()

# Basic listing endpoints
@app.get("/api/users")
def list_users():
    db = SessionLocal()
    try:
        items = db.query(UserAccount).all()
        return [
            {
                "emby_user_id": x.emby_user_id,
                "username": x.username,
                "tg_id": x.tg_id,
                "status": x.status,
                "expires_at": x.expires_at,
            }
            for x in items
        ]
    finally:
        db.close()

@app.get("/api/renewal_codes")
def list_codes():
    db = SessionLocal()
    try:
        items = db.query(RenewalCode).all()
        return [
            {
                "code": x.code,
                "days": x.days,
                "expired_at": x.expired_at,
                "redeemed_by": x.redeemed_by,
                "redeemed_at": x.redeemed_at,
            }
            for x in items
        ]
    finally:
        db.close()
