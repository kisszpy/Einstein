"""请求级依赖：解析当前工作区、校验开放 API 的 Bearer 密钥。"""

from fastapi import Header, HTTPException, Request, status

from app.services import apikeys, workspaces


def current_workspace(x_workspace: str | None = Header(default=None)) -> str:
    """控制台侧的工作区上下文：前端用 X-Workspace 头传递，未知值回落默认工作区。"""
    return workspaces.resolve(x_workspace)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


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
