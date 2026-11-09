import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

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
from app.api.routes import alerts as alert_routes
from app.api.routes import models as model_routes
from app.api.routes import templates as template_routes
from app.api.routes import observability, openapi, platform, projects
from app.api.routes import jobs
from app.core.config import get_settings
from app.services import audit, metrics, ratelimit, tickets

# 控制台无需登录即可访问的端点：登录本身与健康探针
PUBLIC_API_PATHS = {"/api/auth/login", "/api/health"}

# viewer（只读角色）放行的写路径：对话消费类——看得了就要问得了
VIEWER_ALLOWED_PREFIXES = ("/api/chat", "/api/agent", "/api/conversations", "/api/auth/")

# 检查点清理周期。TTL 是小时级，每小时扫一遍足够，代价是每条会话一次读。
CHECKPOINT_SWEEP_SECONDS = 3600


@asynccontextmanager
async def lifespan(app: FastAPI):
    """检查点的生命周期：启动清一次、运行时定期清、退出时关连接。

    不清的话检查点只增不减（每个会话留一条完整消息链），长期运行的库会慢慢
    变大；不关连接的话 sqlite 的 WAL 文件和 pg 的连接会挂在进程退出后。
    """
    from app.services import checkpoints

    # 安全基线：prod 下不允许用默认密钥启动（默认值等于把 MCP 凭证明文放桌上）。
    _s = get_settings()
    if _s.secret_key == "einstein-local-secret-change-me":
        if _s.env == "prod":
            raise RuntimeError(
                "APP_ENV=prod 时必须设置 APP_SECRET_KEY（≥32 位随机串），"
                "否则 MCP 凭证加密形同虚设"
            )
        print(
            "[security] APP_SECRET_KEY 仍是默认值，凭证加密不具备保护作用；"
            "生产部署请设置 APP_ENV=prod 并配置密钥",
            flush=True,
        )

    try:
        await checkpoints.purge_expired()
    except Exception as exc:  # noqa: BLE001 — 清理失败不能挡住启动
        print(f"[checkpoint] 启动清理失败：{type(exc).__name__}: {exc}", flush=True)

    async def sweeper() -> None:
        while True:
            await asyncio.sleep(CHECKPOINT_SWEEP_SECONDS)
            try:
                await checkpoints.purge_expired()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 单次失败不影响下一轮
                print(f"[checkpoint] 定期清理失败：{type(exc).__name__}: {exc}", flush=True)

    async def alert_loop(minutes: int) -> None:
        """定期评估告警规则。

        与"手动立即检查"共用 `alerts.evaluate_all()`，两条路径一套逻辑——
        否则页面上说没事、定时器却判定触发，谁也不知道该信哪个。
        """
        from app.services import alerts as alert_service

        while True:
            await asyncio.sleep(minutes * 60)
            try:
                result = await alert_service.evaluate_all()
                if result.get("fired"):
                    print(f"[alert] 触发 {len(result['fired'])} 条告警", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 单轮失败不影响下一轮
                print(f"[alert] 评估失败：{type(exc).__name__}: {exc}", flush=True)

    task = asyncio.create_task(sweeper())
    interval = int(getattr(get_settings(), "alert_interval_minutes", 0) or 0)
    alert_task = asyncio.create_task(alert_loop(interval)) if interval > 0 else None
    try:
        yield
    finally:
        task.cancel()
        if alert_task is not None:
            alert_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if alert_task is not None:
            try:
                await alert_task
            except asyncio.CancelledError:
                pass
        await checkpoints.close()


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后再试"},
        headers={"Retry-After": str(max(retry_after, 1))},
    )


async def _rate_limit_login(request: Request) -> JSONResponse | None:
    """登录按 IP+账号计数，防撞库。

    读 body 之后必须塞回 `request._body`，否则下游登录接口拿到空流——
    这是 Starlette 的已知行为，不是 hack 的替代品。
    """
    settings = get_settings()
    if not settings.ratelimit_enabled or settings.ratelimit_login <= 0:
        return None
    ip = request.client.host if request.client else "unknown"
    username = ""
    try:
        raw = await request.body()
        request._body = raw  # noqa: SLF001 — Starlette 官方推荐的缓存位
        import json as _json

        username = str(_json.loads(raw or b"{}").get("username", ""))[:64]
    except Exception:  # noqa: BLE001 — 解析失败就按空账号计数，不能因此放行
        pass
    ok, retry = ratelimit.allow(f"login:{ip}:{username}", settings.ratelimit_login, 60)
    return None if ok else _too_many(retry)


def _rate_limit_user(request: Request, path: str) -> JSONResponse | None:
    """已登录用户的分钟限流：对话 / 智能体 / 检索 各自独立计数。

    必须在鉴权中间件**内部**调用。放到外层中间件时 `request.state.user`
    还没写入，`getattr(..., None)` 恒为 None，整段限流会被静默跳过。
    """
    settings = get_settings()
    if not settings.ratelimit_enabled:
        return None
    user = getattr(request.state, "user", None)
    if user is None:
        return None
    if path.startswith("/api/chat"):
        limit, family = settings.ratelimit_chat, "chat"
    elif path.startswith("/api/agent"):
        limit, family = settings.ratelimit_agent, "agent"
    elif path.startswith("/api/kb/search"):
        limit, family = settings.ratelimit_search, "kb"
    else:
        return None
    ok, retry = ratelimit.allow(f"api:{user.id}:{family}", limit, 60)
    return None if ok else _too_many(retry)


def _rate_limit_v1(request: Request) -> JSONResponse | None:
    """开放 API 按 Key 计数的分钟限流。"""
    settings = get_settings()
    if not settings.ratelimit_enabled or settings.ratelimit_openapi <= 0:
        return None
    key = deps._extract_bearer(request.headers.get("authorization"))[:24] or "anon"
    ok, retry = ratelimit.allow(f"v1:{key}", settings.ratelimit_openapi, 60)
    return None if ok else _too_many(retry)


def create_app() -> FastAPI:
    settings = get_settings()
    # debug 固定为 False：Starlette 在 debug=True 时会把完整堆栈直接写进响应体，
    # 泄漏绝对路径与依赖版本。服务端仍会由 uvicorn 打印完整 traceback。
    app = FastAPI(title=settings.app_name, debug=False, lifespan=lifespan)

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
        # 登录限流必须在鉴权之前（此时还没有用户身份），按 IP+账号计数防撞库。
        if path == "/api/auth/login" and request.method == "POST":
            blocked = await _rate_limit_login(request)
            if blocked is not None:
                return blocked
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
            # 工作区级每日配额（0 = 不限；chat/agent 在路由内还会记 Token 用量）。
            # 通过后立刻累加——只 check 不 incr 等于把开关悬空。
            if path.startswith(("/api/chat", "/api/agent")):
                allowed, _used = ratelimit.quota_check(effective_ws, "requests")
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "已达当日请求配额，请明天再试或联系管理员调整"},
                    )
                ratelimit.quota_incr(effective_ws, "requests")
            # 用户级分钟限流。必须放在鉴权之后——放在外层中间件时
            # request.state.user 还没被写入，取不到用户就整段放行了。
            blocked = _rate_limit_user(request, path)
            if blocked is not None:
                return blocked
        elif path.startswith("/v1/"):
            blocked = _rate_limit_v1(request)
            if blocked is not None:
                return blocked
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
    app.include_router(model_routes.router, prefix="/api")
    app.include_router(alert_routes.router, prefix="/api")
    app.include_router(template_routes.router, prefix="/api")
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

    # 项目登记自愈：存量目录收养进 projects 表（归空间所有者），运行时目录
    # （.agent、_scratch 等）跳过——没有这一步，老项目会在升级后集体消失。
    try:
        from app.services import projects as project_registry

        stats = project_registry.adopt_orphans()
        print(
            f"[startup] 项目登记就绪：本次收养 {stats['adopted']} 个，跳过内部目录 {stats['skipped']} 个",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 — 同上，不让元数据问题挡住服务
        print(f"[startup] 项目登记迁移失败：{exc}", flush=True)
    return app


app = create_app()
