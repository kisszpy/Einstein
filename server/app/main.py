import os
import time

# 沙箱/系统代理会让 OpenAI SDK 以绝对路径请求（path 变成 URL 编码的完整地址），
# 被 uvicorn 当作普通 path 解析导致 404。本服务只访问 localhost 资源，直接禁用代理。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import deps
from app.api.routes import agent, auth, chat, conversations, experts, health, kb, mcp, skills
from app.api.routes import eval as eval_routes
from app.api.routes import observability, openapi, platform
from app.core.config import get_settings
from app.services import audit, metrics

# 控制台无需登录即可访问的端点：登录本身与健康探针
PUBLIC_API_PATHS = {"/api/auth/login", "/api/health"}


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug)

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        """控制台全站鉴权：除白名单外，/api/* 一律要求有效会话。

        /v1 走 API Key（各路由自带依赖），不放行到这里。
        """
        path = request.url.path
        if (
            path.startswith("/api/")
            and path not in PUBLIC_API_PATHS
            and request.method != "OPTIONS"
        ):
            token = deps._extract_bearer(request.headers.get("authorization"))
            user = deps.user_from_token(token)
            if user is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "未登录或登录已过期，请重新登录"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.user = user
        return await call_next(request)

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - t0) * 1000
            if request.url.path.startswith("/api/"):
                metrics.record_request(request.method, request.url.path, 500, duration)
            raise
        duration = (time.perf_counter() - t0) * 1000
        path = request.url.path
        # 观测端点自身不记录，避免自激励
        if path.startswith("/api/") and not path.startswith("/api/obs"):
            metrics.record_request(request.method, path, response.status_code, duration)
        # 开放 API 全量审计：谁（哪个密钥/工作区）在何时调了什么、花了多少 token
        if path.startswith("/v1/") and not getattr(request.state, "audit_self", False):
            key = getattr(request.state, "api_key", None)
            tokens_in, tokens_out = getattr(request.state, "audit_tokens", (0, 0))
            audit.record(
                workspace_id=getattr(key, "workspace_id", "-"),
                key_id=getattr(key, "id", None),
                key_name=getattr(key, "name", None),
                method=request.method,
                path=path,
                status=response.status_code,
                duration_ms=duration,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                detail=getattr(request.state, "audit_detail", ""),
            )
        return response

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(kb.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(experts.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(observability.router, prefix="/api")
    app.include_router(eval_routes.router, prefix="/api")
    app.include_router(platform.router, prefix="/api")
    # 对外开放 API：/v1/...（Bearer 鉴权，与 /api 控制台面分开）
    app.include_router(openapi.router)
    return app


app = create_app()
