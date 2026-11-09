import logging
import os
import time

# 沙箱/系统代理会让 OpenAI SDK 以绝对路径请求（path 变成 URL 编码的完整地址），
# 被 uvicorn 当作普通 path 解析导致 404。本服务只访问 localhost 资源，直接禁用代理。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import deps
from app.api.routes import agent, auth, browse, chat, conversations, experts, health, kb, mcp, skills
from app.api.routes import eval as eval_routes
from app.api.routes import memory as memory_routes
from app.api.routes import observability, openapi, platform, projects
from app.api.routes import jobs
from app.core.config import get_settings
from app.services import audit, metrics, ratelimit, tickets

# 控制台无需登录即可访问的端点：登录本身与健康探针
PUBLIC_API_PATHS = {"/api/auth/login", "/api/health"}

# viewer（只读角色）放行的写路径：对话消费类——看得了就要问得了
VIEWER_ALLOWED_PREFIXES = ("/api/chat", "/api/agent", "/api/conversations", "/api/auth/")


def create_app() -> FastAPI:
    settings = get_settings()
    # debug 固定为 False：Starlette 在 debug=True 时会把完整堆栈直接写进响应体，
    # 泄漏绝对路径与依赖版本。服务端仍会由 uvicorn 打印完整 traceback。
    app = FastAPI(title=settings.app_name, debug=False)

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("app.errors").exception(
            "unhandled error on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请稍后重试或联系管理员"},
        )

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        """控制台全站鉴权：除白名单外，/api/* 一律要求有效会话。

        例外是「预览票据」：iframe / img 带不了 Authorization 头，改用短期
        票据（只对 GET + 预览前缀生效），见 services/tickets.py。
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
                grant = tickets.resolve(
                    tickets.token_from(path, request.query_params.get("ticket")), path, request.method
                )
                if grant is not None:
                    user = deps.users.get_user_store().get(grant["user_id"])
                    if user is not None and not user.disabled:
                        request.state.user = user
                        request.state.ticket = grant
            if user is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "未登录或登录已过期，请重新登录"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.user = user
            # 工作区上下文供 llm 层记 Token 配额（ContextVar 随请求传播）。
            # 必须走「用户实际有权」的判定，不能直接用请求头——否则改个头就能
            # 消耗别的空间的配额，或在别人的空间里刷额度。
            requested_ws = deps.workspaces.resolve(request.headers.get("x-workspace"))
            effective_ws = (
                requested_ws
                if deps.workspace_role(user, requested_ws)
                else (deps.allowed_workspaces(user) or [requested_ws])[0]
            )
            ratelimit.current_ws.set(effective_ws)
            # 只读拦截按「当前空间的角色」判定，不是账号的全局角色：
            # 同一个人在自己的空间可写、在被邀请的空间只读，两种状态并存
            ws_role = deps.workspace_role(user, effective_ws)
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and not deps.members.at_least(ws_role, "member")
                and not path.startswith(VIEWER_ALLOWED_PREFIXES)
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "只读角色无权执行该操作，请联系管理员调整角色"},
                )
            # 工作区级每日配额（0 = 不限；chat/agent 在路由内还会记 Token 用量）
            if path.startswith(("/api/chat", "/api/agent")):
                allowed, _used = ratelimit.quota_check(effective_ws, "requests")
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "已达当日请求配额，请明天再试或联系管理员调整"},
                    )
        return await call_next(request)

    @app.middleware("http")
    async def _ratelimit_middleware(request: Request, call_next):
        """按端点族的固定窗口限流：防爆破、防打爆，Redis 计数、内存兜底。

        键维度不同：登录按 IP+账号（防撞库），其余按用户（登录态）或
        API Key（/v1）。设为 0 即关闭对应限流。
        """
        settings = get_settings()
        if not settings.ratelimit_enabled:
            return await call_next(request)
        path = request.url.path
        window = 60  # 秒
        if path == "/api/auth/login" and request.method == "POST":
            ip = request.client.host if request.client else "unknown"
            username = ""
            try:
                # 读掉 body 后必须塞回 request._body，否则下游登录接口拿到空流
                raw = await request.body()
                request._body = raw  # noqa: SLF001 — Starlette 官方推荐的缓存位
                import json as _json

                username = str(_json.loads(raw or b"{}").get("username", ""))[:64]
            except Exception:  # noqa: BLE001
                pass
            ok, retry = ratelimit.allow(f"login:{ip}:{username}", settings.ratelimit_login, window)
        elif path.startswith("/v1/"):
            key = deps._extract_bearer(request.headers.get("authorization"))[:24] or "anon"
            ok, retry = ratelimit.allow(f"v1:{key}", settings.ratelimit_openapi, window)
        elif path.startswith(("/api/chat", "/api/kb/search", "/api/agent/run")):
            user = getattr(request.state, "user", None)
            if user is None:   # 未登录请求由鉴权中间件处理，这里不重复拦
                return await call_next(request)
            if path.startswith("/api/chat"):
                limit = settings.ratelimit_chat
            elif path.startswith("/api/agent"):
                limit = settings.ratelimit_agent
            else:
                limit = settings.ratelimit_search
            ok, retry = ratelimit.allow(f"api:{user.id}:{path.split('/')[2]}", limit, window)
        else:
            ok, retry = True, 0
        if not ok:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": str(retry)},
            )
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
    app.include_router(projects.router, prefix="/api")
    app.include_router(browse.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(kb.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(experts.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(observability.router, prefix="/api")
    app.include_router(eval_routes.router, prefix="/api")
    app.include_router(platform.router, prefix="/api")
    app.include_router(memory_routes.router, prefix="/api")
    # 对外开放 API：/v1/...（Bearer 鉴权，与 /api 控制台面分开）
    app.include_router(openapi.router)

    # 启动时回收过期的批次暂存目录：分片任务删不掉别人还没处理的文件，
    # 批次目录只能靠时间兜底清理（Windows 上文件被占用时尤其容易残留）
    try:
        from app.services import batch as batch_io

        removed = batch_io.purge_stale_batches(
            kb.UPLOAD_DIR, max_age_hours=settings.import_staging_ttl_hours
        )
        if removed:
            print(f"[startup] 清理过期批次暂存目录 {removed} 个", flush=True)
    except Exception as exc:  # noqa: BLE001 — 清理失败不能挡住启动
        print(f"[startup] 清理批次暂存目录失败：{exc}", flush=True)

    # RBAC 成员关系自愈：存量 user.workspaces 迁成成员表，并给每个账号补个人空间。
    # 幂等且开销极小（几毫秒），放在每次启动跑，升级后不需要手工执行迁移。
    try:
        from app.services import members

        stats = members.migrate()
        print(
            f"[startup] RBAC 成员关系就绪：{stats['memberships']} 条（本次补 {stats['created']}）",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 — 迁移失败不阻塞启动，但要把原因打出来
        print(f"[startup] RBAC 迁移失败：{exc}", flush=True)
    return app


app = create_app()
