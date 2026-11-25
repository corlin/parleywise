"""账号、认证与元数据路由。"""
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth
from ..deps import CONCLUSIONS, REJECT_REASONS, RULES, SECURE_COOKIE, SOURCES, _s, me

router = APIRouter()
_registration_attempts = {}


@router.get("/api/health")
def health():
    return {"ok": True}


@router.get("/api/registration")
def registration_status():
    return {"enabled": bool(os.environ.get("PARLEYWISE_INVITE_CODE"))}


@router.post("/api/register")
async def register(request: Request):
    code = os.environ.get("PARLEYWISE_INVITE_CODE", "")
    if not code:
        raise HTTPException(403, "当前未开放注册，请联系管理员")
    peer = request.client.host if request.client else "unknown"
    attempts = [t for t in _registration_attempts.get(peer, []) if time.time() - t < 300]
    if len(attempts) >= 5:
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    attempts.append(time.time())
    _registration_attempts[peer] = attempts
    body = await request.json()
    if not hmac.compare_digest(str(body.get("invite_code") or "").encode(), code.encode()):
        raise HTTPException(403, "邀请码不正确")
    try:
        user = auth.register(_s(body.get("username")), _s(body.get("name")), str(body.get("password") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return user


@router.patch("/api/profile")
async def update_profile(request: Request):
    body = await request.json()
    try:
        return auth.update_profile(me(request)["id"], _s(body.get("name")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/profile/password")
async def change_password(request: Request):
    body = await request.json()
    try:
        auth.change_password(me(request)["id"], str(body.get("old_password") or ""), str(body.get("new_password") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@router.post("/api/login")
async def do_login(request: Request):
    body = await request.json()
    if not auth.has_users():
        raise HTTPException(400, "还没有账号，请先在服务器上运行：uv run python -m app.auth add <用户名> <姓名> --admin")
    token, res = auth.login(_s(body.get("username")), str(body.get("password") or ""))
    if not token:
        raise HTTPException(401, res)
    resp = JSONResponse(res)
    resp.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                    secure=SECURE_COOKIE)
    return resp


@router.post("/api/logout")
def do_logout(request: Request):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@router.get("/api/meta")
def meta(request: Request):
    return {
        "user": me(request),
        "reject_reasons": REJECT_REASONS,
        "conclusions": CONCLUSIONS,
        "key_configured": bool(os.environ.get("LITELLM_API_KEY")),
        "sources": SOURCES,
        "rules": list(RULES.values()),
    }
