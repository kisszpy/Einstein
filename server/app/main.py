import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager

# 沙箱/系统代理会让 OpenAI SDK 以绝对路径请求（path 变成 URL 编码的完整地址），
# 被 uvicorn 当作普通 path 解析导致 404。本服务只访问 localhost 资源，直接禁用代理。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.api import deps
from app.api.routes import (
    agent,
    alerts as alert_routes,
    auth,
    browse,
    chat,
    conversations,
    eval as eval_routes,
    experts,
    health,
    jobs,
    kb,
    mcp,
    memory as memory_routes,
    models as model_routes,
    observability,
    openapi,
    platform,
    projects,
    ppt as ppt_routes,
    public as public_routes,
    shares as share_routes,
    skills,
    templates as template_routes,
)
from app.core.config import get_settings
from app.core.logging import get_logger, request_id, setup_logging
from app.services import audit, metrics, ratelimit, tickets

logger = get_logger("app.main")

# 控制台无需登录即可访问的端点：登录本身与健康探针
PUBLIC_API_PATHS = {
    "/api/auth/login",
    # 邮箱验证码的两步都在登录之前，必须公开
    "/api/auth/email/code",
    "/api/auth/email/verify",
    "/api/health",
}

# viewer（只读角色）放行的写路径：对话消费类——看得了就要问得了
VIEWER_ALLOWED_PREFIXES = ("/api/chat", "/api/agent", "/api/conversations", "/api/auth/")

# iframe / img 鉴权失败时回这张页面：它们没有 Authorization 头，之前只能拿到一段
# JSON，被浏览器原样画进预览面板。给个能读懂的原因 + 一个「重新打开」按钮。
_PREVIEW_AUTH_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>预览需要重新打开</title></head>
<body style="margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;
font:14px/1.6 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#3f3f46;background:#fafafa">
<div style="max-width:380px;padding:28px;text-align:center">
  <p style="font-size:15px;font-weight:600;margin:0 0 8px">这个预览的访问凭据过期了</p>
  <p style="margin:0 0 18px;color:#71717a">
    预览链接只有 15 分钟有效期，长时间停留或服务重启后会失效。
    点下面的按钮重新打开，文件本身没有丢。
  </p>
  <button onclick="location.reload()" style="border:0;border-radius:8px;padding:8px 16px;
  font:inherit;color:#fff;background:#18181b;cursor:pointer">重新打开预览</button>
  <p style="margin:18px 0 0;font-size:12px;color:#a1a1aa">
    还是不行就关掉预览面板，重新点开这个文件。
  </p>
</div></body></html>"""

# 检查点清理周期。TTL 是小时级，每小时扫一遍足够，代价是每条会话一次读。
CHECKPOINT_SWEEP_SECONDS = 3600


@asynccontextmanager
async def lifespan(app: FastAPI):
    """检查点的生命周期：启动清一次、运行时定期清、退出时关连接。

    不清的话检查点只增不减（每个会话留一条完整消息链），长期运行的库会慢慢
    变大；不关连接的话 sqlite 的 WAL 文件和 pg 的连接会挂在进程退出后。
    """
    from app.services import checkpoints

    # uvicorn 的 configure_logging 可能在应用导入之后才跑（嵌入式启动、测试里
    # 直接起 Server 都是这个顺序），会把 uvicorn.* 的 handler 再装回来。
    # 启动时强制重挂一次，保证「谁先谁后都输出同一种格式」。
    setup_logging(force=True)

    # 安全基线：prod 下不允许用默认密钥启动（默认值等于把 MCP 凭证明文放桌上）。
    _s = get_settings()
    if _s.secret_key == "einstein-local-secret-change-me":
        if _s.env == "prod":
            raise RuntimeError(
                "APP_ENV=prod 时必须设置 APP_SECRET_KEY（≥32 位随机串），"
                "否则 MCP 凭证加密形同虚设"
            )
        logger.warning(
            "APP_SECRET_KEY 仍是默认值，凭证加密不具备保护作用；"
            "生产部署请设置 APP_ENV=prod 并配置密钥"
        )

    try:
        await checkpoints.purge_expired()
    except Exception as exc:  # noqa: BLE001 — 清理失败不能挡住启动
        logger.warning("检查点启动清理失败：%s: %s", type(exc).__name__, exc)

    # 上一个进程被杀时，正在跑的那几次运行没机会写终态事件。库里留着它们，
    # `/runs/active` 每次刷新都把它们当「还在跑」返回，前端挂上一条永不结束
    # 的流，`running` 于是锁住整个页面——历史任务切不动，只能点停止，而停止
    # 又因为内存里没有对应任务而 404。启动这一刻内存登记簿必定为空，正好把
    # 这些尸体统一收尾。
    if _s.agent_run_orphan_sweep:
        from datetime import datetime, timezone

        from app.services import run_events as run_events_svc

        try:
            boot = datetime.now(timezone.utc).isoformat(timespec="seconds")
            n = run_events_svc.abandon_orphans(before=boot)
            if n:
                logger.info("收尾上次进程遗留的未完成运行：%d 条", n)
        except Exception as exc:  # noqa: BLE001 — 收尾失败不该挡住启动
            logger.warning("遗留运行收尾失败：%s: %s", type(exc).__name__, exc)

    async def sweeper() -> None:
        while True:
            await asyncio.sleep(CHECKPOINT_SWEEP_SECONDS)
            try:
                await checkpoints.purge_expired()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 单次失败不影响下一轮
                logger.warning("检查点定期清理失败：%s: %s", type(exc).__name__, exc)

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
                    logger.info("告警评估触发 %d 条", len(result["fired"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 单轮失败不影响下一轮
                logger.warning("告警评估失败：%s: %s", type(exc).__name__, exc)

    async def _warm_deck() -> None:
        """预热 SVG→PPT 转换器。

        首次编译 1.5s 全花在 skia / 字体库初始化上，与页数无关；热起来之后
        同样一份 deck 只要 0.25s。不预热的话这笔钱由「第一个点开设计稿的人」
        付。延迟几秒再跑，避开启动时的连接池/迁移高峰。
        """
        from app.services.capabilities import svgdeck

        try:
            await asyncio.sleep(3)
            await asyncio.to_thread(svgdeck.warm_up)
            logger.debug("svgdeck 转换器预热完成")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 纯优化，失败不影响任何功能
            logger.debug("svgdeck 预热跳过：%s", exc)

    task = asyncio.create_task(sweeper())
    warm_task = asyncio.create_task(_warm_deck())
    interval = int(getattr(get_settings(), "alert_interval_minutes", 0) or 0)
    alert_task = asyncio.create_task(alert_loop(interval)) if interval > 0 else None
    try:
        yield
    finally:
        task.cancel()
        warm_task.cancel()
        if alert_task is not None:
            alert_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # 预期路径：取消后 await 必然抛 CancelledError
        if alert_task is not None:
            try:
                await alert_task
            except asyncio.CancelledError:
                pass  # 预期路径：取消后 await 必然抛 CancelledError
        try:
            await warm_task
        except asyncio.CancelledError:
            pass  # 预期路径：预热还没跑完就被要求停机
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
    # 最先做：后面 create_app 里的启动自愈、异常处理器都要打日志，
    # 晚一步的话这些日志会因为 root 没有 handler 而走 lastResort 丢格式。
    setup_logging()
    # debug 固定为 False：Starlette 在 debug=True 时会把完整堆栈直接写进响应体，
    # 泄漏绝对路径与依赖版本。服务端仍会由 uvicorn 打印完整 traceback。
    app = FastAPI(
        title=settings.app_name, version="1.0.0", debug=False, lifespan=lifespan
    )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # 带上请求 id + 方法路径：日志里能直接对上前端拿到的 X-Request-Id
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
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
            # 分享出去的免登录链接：权限在 token 里，不在会话里。
            # 这些路由自己按 IP 限流，不经过这里的用户态配额。
            and not path.startswith("/api/public/")
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
                # iframe / img / <a download> 带不了 Authorization 头，失败时浏览器会把
                # JSON 原样渲染在预览面板里——用户看到的就是
                # {"detail":"未登录或登录已过期，请重新登录"} 这串机器话。
                # 这类请求改回一张能读懂、能自救的页面。
                if not request.headers.get("authorization") and path.startswith(
                    tickets.ALLOWED_PREFIXES
                ):
                    return HTMLResponse(_PREVIEW_AUTH_PAGE, status_code=401)
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

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        """给每个请求一个追踪 id，贯穿日志与响应头。

        必须最后注册——`@app.middleware` 是「后注册的在外层」，这里要包住鉴权和
        metrics，它们打出来的 401、慢请求日志才会带同一个 id。客户端带了
        x-request-id 就沿用，前端把浏览器侧报错贴过来时能直接对上台服日志。
        """
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:32]
        # 刻意不 reset：SSE 的响应体是在 call_next 返回**之后**才迭代的，
        # reset 掉的话流式生成阶段（chat/agent 的 token 转发日志）里
        # request_id 全变成 "-"，恰好是排障最需要它的场景。
        # 每个请求跑在独立 asyncio task 里，contextvar 天然隔离，不 reset 不会串号。
        request_id.set(rid)
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
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
    app.include_router(ppt_routes.router, prefix="/api")
    app.include_router(share_routes.router, prefix="/api")
    # 免登录访问面：token 即权限（中间件对 /api/public/ 前缀放行）
    app.include_router(public_routes.router, prefix="/api")
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
            logger.info("清理过期批次暂存目录 %d 个", removed)
    except Exception as exc:  # noqa: BLE001 — 清理失败不能挡住启动
        logger.warning("清理批次暂存目录失败：%s: %s", type(exc).__name__, exc)

    # RBAC 成员关系自愈：存量 user.workspaces 迁成成员表，并给每个账号补个人空间。
    # 幂等且开销极小（几毫秒），放在每次启动跑，升级后不需要手工执行迁移。
    try:
        from app.services import members

        stats = members.migrate()
        logger.info(
            "RBAC 成员关系就绪：%d 条（本次补 %d）", stats["memberships"], stats["created"]
        )
    except Exception as exc:  # noqa: BLE001 — 迁移失败不阻塞启动，但要把原因打出来
        logger.warning("RBAC 迁移失败：%s: %s", type(exc).__name__, exc)

    # 项目登记自愈：存量目录收养进 projects 表（归空间所有者），运行时目录
    # （.agent、_scratch 等）跳过——没有这一步，老项目会在升级后集体消失。
    try:
        from app.services import projects as project_registry

        stats = project_registry.adopt_orphans()
        logger.info(
            "项目登记就绪：本次收养 %d 个，跳过内部目录 %d 个",
            stats["adopted"], stats["skipped"],
        )
    except Exception as exc:  # noqa: BLE001 — 同上，不让元数据问题挡住服务
        logger.warning("项目登记迁移失败：%s: %s", type(exc).__name__, exc)
    return app


app = create_app()
