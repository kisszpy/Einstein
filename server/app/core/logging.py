"""统一日志。

三条约束决定了这里的写法：

1. **只往 stdout 写**。容器里由 Docker / k8s 采集，落文件反而要管轮转和权限。
2. **每条要能对上「哪个请求」**。排障时没有 request_id，只能拿时间戳去猜——
   并发下根本对不上。id 存在 contextvar 里，中间件写入，格式器读出来。
3. **第三方库要降噪**。httpx / openai 默认 INFO 会把业务日志淹掉，统一压到
   WARNING；真要排查时用 `APP_LOG_LEVEL=DEBUG` 打开自家模块的 debug。

用法：

    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("…")

应用代码里不要用 print：它绕过级别控制、带不出 request_id，也进不了采集。
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

# 当前请求的追踪 id。非请求上下文（worker、启动阶段）取到默认值 "-"。
request_id: ContextVar[str] = ContextVar("request_id", default="-")

_FMT = "%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s: %(message)s"

# 这些 logger 由 uvicorn 自己装过 handler。不清掉的话同一条日志会打两遍，
# 而且格式一套带颜色一套不带，看起来像两个服务在抢 stdout。
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# 默认 INFO 太吵的第三方库：它们的每条日志都是「我发了个请求」，
# 对排障没有增量信息，只有打开 DEBUG 时才需要。
_NOISY_LOGGERS = (
    "httpx", "httpcore", "urllib3", "openai", "asyncio",
    "multipart", "python_multipart", "PIL", "matplotlib",
)

_configured = False


class _RequestIdFilter(logging.Filter):
    """把 contextvar 里的 request_id 贴到每条 record 上。

    用 Filter 而不是自定义 Formatter：这样经由 root 的所有 handler（包括
    测试里临时挂的）都能拿到这个字段，不会因为少一个 key 抛 KeyError。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def _resolve_level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    raw = (level or "").strip().upper()
    if raw.isdigit():
        return int(raw)
    # getattr(logging, "INFO")；非法值退回 INFO，配置写错不该让服务起不来
    return getattr(logging, raw, logging.INFO) if raw else logging.INFO


def setup_logging(level: str | int | None = None, *, force: bool = False) -> None:
    """配置 root logger。幂等——重复调用（多次 create_app、被测试反复导入）只生效一次。

    level 为空时读 `settings.log_level`；配置里也没有就用 INFO。

    `force=True` 重新挂一遍 handler：uvicorn 的 `configure_logging()` 在
    「先导入应用、后起 Server」的路径上（嵌入式部署、pytest 里直接起 Server）
    会晚于这里执行，把它自己的 handler 装回去，日志就变成两套格式。
    启动钩子里强制重挂一次即可对齐，代价是 root 上多挂一次 StreamHandler
    的开销（旧 handler 会被 close 掉，不泄漏）。
    """
    global _configured
    if _configured and not force:
        return
    _configured = True

    if level is None:
        try:
            from app.core.config import get_settings

            level = get_settings().log_level
        except Exception:  # noqa: BLE001 — 配置没就绪也要能打日志
            level = "INFO"
    resolved = _resolve_level(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(_RequestIdFilter())

    # 先清 uvicorn 已装的 handler，再统一挂到 root：格式一致、不会重复输出
    for name in _UVICORN_LOGGERS:
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(resolved, logging.WARNING))


def get_logger(name: str) -> logging.Logger:
    """取模块级 logger。名字一律传 `__name__`，日志里才看得出是哪个模块。"""
    return logging.getLogger(name)


def current_request_id() -> str:
    return request_id.get()
