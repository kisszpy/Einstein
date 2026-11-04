import os
import time

# 沙箱/系统代理会让 OpenAI SDK 以绝对路径请求（path 变成 URL 编码的完整地址），
# 被 uvicorn 当作普通 path 解析导致 404。本服务只访问 localhost 资源，直接禁用代理。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import agent, chat, conversations, experts, health, kb, mcp, skills
from app.api.routes import eval as eval_routes
from app.api.routes import observability
from app.core.config import get_settings
from app.services import metrics


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug)

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
        return response

    app.include_router(health.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(kb.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(experts.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(observability.router, prefix="/api")
    app.include_router(eval_routes.router, prefix="/api")
    return app


app = create_app()
