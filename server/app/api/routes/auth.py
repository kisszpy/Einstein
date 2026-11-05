"""登录、会话与成员管理。

- `/auth/login` 是唯一公开接口（健康检查除外），签发控制台会话令牌；
- 其余 `/api/*` 由 `main.py` 的中间件统一拦未登录请求；
- 成员管理仅管理员可用，删除/禁用会同时吊销该用户全部会话。
"""

import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import (
    _extract_bearer,
    allowed_workspaces,
    current_admin,
    current_user,
)
from app.services import audit, sessions, users, workspaces

router = APIRouter(tags=["auth"])

# 登录失败节流：同一用户名连续 5 次失败锁定 60 秒，挡住脚本撞库
_MAX_FAILS = 5
_LOCK_SECONDS = 60
_fails: dict[str, tuple[int, float]] = {}
_fail_lock = threading.Lock()


def _throttled(username: str) -> int:
    """返回剩余锁定秒数，0 表示放行。"""
    with _fail_lock:
        count, until = _fails.get(username.lower(), (0, 0.0))
    remain = until - time.monotonic()
    if count >= _MAX_FAILS and remain > 0:
        return int(remain) + 1
    return 0


def _note_failure(username: str) -> None:
    key = username.lower()
    with _fail_lock:
        count, until = _fails.get(key, (0, 0.0))
        count = count + 1 if until > time.monotonic() else 1
        _fails[key] = (count, time.monotonic() + _LOCK_SECONDS if count >= _MAX_FAILS else until)


def _clear_failures(username: str) -> None:
    with _fail_lock:
        _fails.pop(username.lower(), None)


def _audit(username: str, status: int, detail: str) -> None:
    audit.record(
        workspace_id="-",
        key_id=None,
        key_name=f"user:{username}",
        method="POST",
        path="/api/auth/login",
        status=status,
        duration_ms=0.0,
        detail=detail,
    )


@router.post("/auth/login")
async def login(req: Request, payload: dict) -> dict:
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        raise HTTPException(400, "请输入用户名与口令")

    wait = _throttled(username)
    if wait:
        _audit(username, 429, "失败次数过多，已临时锁定")
        raise HTTPException(429, f"失败次数过多，请 {wait} 秒后再试")

    user = users.get_user_store().authenticate(username, password)
    if user is None:
        _note_failure(username)
        _audit(username, 401, "用户名或口令错误")
        raise HTTPException(401, "用户名或口令错误")

    _clear_failures(username)
    store = users.get_user_store()
    store.save()
    token, session = sessions.get_session_store().issue(
        user.id, req.headers.get("user-agent", "")
    )
    sessions.get_session_store().save()
    _audit(username, 200, f"登录成功（{user.role}）")
    return {
        "token": token,
        "expires_at": session.expires_at,
        "user": user.to_public(),
        "workspaces": allowed_workspaces(user),
    }


@router.get("/auth/me")
async def me(request: Request, user: users.User = Depends(current_user)) -> dict:
    session = sessions.get_session_store().resolve(
        _extract_bearer(request.headers.get("authorization"))
    )
    return {
        "user": user.to_public(),
        "workspaces": allowed_workspaces(user),
        "session_expires_at": session.expires_at if session else None,
    }


@router.post("/auth/logout")
async def logout(request: Request, user: users.User = Depends(current_user)) -> dict:
    token = _extract_bearer(request.headers.get("authorization"))
    sessions.get_session_store().revoke(token)
    sessions.get_session_store().save()
    return {"ok": True, "user": user.username}


# ---------- 成员管理（管理员） ----------


@router.get("/users")
async def list_users(admin: users.User = Depends(current_admin)) -> list[dict]:
    return [u.to_public() for u in users.get_user_store().list_all()]


@router.post("/users")
async def create_user(
    payload: dict, admin: users.User = Depends(current_admin)
) -> dict:
    registry = workspaces.get_workspace_store()
    targets = [w for w in payload.get("workspaces") or [] if registry.get(w)]
    try:
        user = users.get_user_store().create(
            username=str(payload.get("username", "")),
            password=str(payload.get("password", "")),
            display_name=str(payload.get("display_name", "")),
            role=str(payload.get("role", "member")),
            workspaces=targets or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    users.get_user_store().save()
    return user.to_public()


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str, payload: dict, admin: users.User = Depends(current_admin)
) -> dict:
    target = users.get_user_store().get(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    if target.id == admin.id and payload.get("role") and payload["role"] != target.role:
        raise HTTPException(400, "不能修改自己的角色")
    if target.id == admin.id and payload.get("disabled"):
        raise HTTPException(400, "不能禁用自己的账号")
    try:
        users.get_user_store().update(
            target,
            display_name=payload.get("display_name"),
            role=payload.get("role"),
            workspaces=payload.get("workspaces"),
            disabled=payload.get("disabled"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if target.disabled:
        sessions.get_session_store().revoke_user(target.id)
        sessions.get_session_store().save()
    users.get_user_store().save()
    return target.to_public()


@router.post("/users/{user_id}/password")
async def reset_password(
    user_id: str, payload: dict, admin: users.User = Depends(current_admin)
) -> dict:
    target = users.get_user_store().get(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    try:
        users.get_user_store().set_password(target, str(payload.get("password", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    users.get_user_store().save()
    # 改口令后旧会话立即失效，避免口令泄露后仍未下线
    sessions.get_session_store().revoke_user(target.id)
    sessions.get_session_store().save()
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin: users.User = Depends(current_admin)) -> dict:
    store = users.get_user_store()
    target = store.get(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    if target.id == admin.id:
        raise HTTPException(400, "不能删除自己的账号")
    if target.role == "owner" and len([u for u in store.admins() if u.role == "owner"]) <= 1:
        raise HTTPException(400, "至少保留一个所有者账号")
    sessions.get_session_store().revoke_user(target.id)
    sessions.get_session_store().save()
    store.delete(user_id)
    store.save()
    return {"ok": True}
