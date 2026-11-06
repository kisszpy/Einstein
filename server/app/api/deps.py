"""请求级依赖：会话鉴权、当前用户、工作区收敛、开放 API 的 Bearer 密钥。"""

from fastapi import Depends, Header, HTTPException, Request, status

from app.services import apikeys, sessions, users, workspaces


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def user_from_token(token: str) -> users.User | None:
    """令牌 → 用户。会话失效、用户被禁用或删除都视同未登录。"""
    session = sessions.get_session_store().resolve(token)
    if session is None:
        return None
    user = users.get_user_store().get(session.user_id)
    if user is None or user.disabled:
        return None
    return user


def current_user(request: Request, authorization: str | None = Header(default=None)) -> users.User:
    """控制台请求的当前用户；中间件已解析过就直接复用。"""
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    user = user_from_token(_extract_bearer(authorization))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user = user
    return user


def current_admin(user: users.User = Depends(current_user)) -> users.User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def allowed_workspaces(user: users.User) -> list[str]:
    """该用户可见的工作区；管理员返回全部已注册工作区。"""
    registry = workspaces.get_workspace_store()
    existing = [w.id for w in registry.list()]
    if user.is_admin:
        return existing
    return [w for w in existing if w in user.workspaces]


def current_workspace(
    request: Request,
    x_workspace: str | None = Header(default=None),
) -> str:
    """控制台侧的工作区上下文。

    管理员可切任意已知工作区；普通成员只能落在自己归属的工作区内，
    请求了不属于自己的工作区时回落到其第一个可用工作区，不跨租户读取。
    """
    requested = workspaces.resolve(x_workspace)
    user = getattr(request.state, "user", None)
    # iframe / img 带不了 X-Workspace 头，工作区以票据绑定的为准，
    # 否则预览会掉到 default 工作区里 404。
    ticket = getattr(request.state, "ticket", None)
    if ticket and not x_workspace:
        return ticket.get("workspace") or requested
    if user is None:
        return requested
    if user.can_access(requested):
        return requested
    visible = allowed_workspaces(user)
    return visible[0] if visible else workspaces.DEFAULT_ID


def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
) -> apikeys.ApiKey:
    """对外开放 API 的鉴权：Authorization: Bearer sk-xxx。

    校验通过后把密钥挂到 request.state，便于审计中间件读取。
    """
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 API Key：请在 Authorization 头中携带 Bearer sk-xxx",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key = apikeys.get_api_key_store().verify(token)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key 无效或已吊销",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.api_key = key
    return key


def require_scope(key: apikeys.ApiKey, scope: str) -> None:
    if "*" in key.scopes or scope in key.scopes:
        return
    raise HTTPException(status_code=403, detail=f"该密钥没有 {scope} 权限")
