"""限流与配额：固定窗口计数（Redis为主，内存兜底）。

为什么自写而不用 slowapi 之类：规则要按"登录按 IP+账号、对话按用户、
/v1 按 API Key"分别取键，通用装饰器反而绕。实现保持极简——
固定窗口（INCR + EXPIRE）精度够用且原子性由 Redis 保证。

配额（quota）与限流的区别：限流防的是"短时间打爆"，按分钟窗口；
配额防的是"一天用超"，按自然日计数，超了当天 429 到次日重置。
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.config import get_settings

# 当前请求的工作区（鉴权中间件里 set），llm 层用它记 Token 配额
current_ws: ContextVar[str] = ContextVar("current_ws", default="")

_memory: dict[str, tuple[int, int]] = {}   # key -> (count, window_end)
_lock = threading.Lock()

_redis = None
_redis_tried = False


def _get_redis():
    """复用 settings.redis_url 的连接；Redis 不在就静默走内存。"""
    global _redis, _redis_tried
    if _redis_tried:
        return _redis
    _redis_tried = True
    settings = get_settings()
    if settings.queue_backend != "redis" or not settings.redis_url:
        return None
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(settings.redis_url, socket_timeout=0.5)
        client.ping()
        _redis = client
    except Exception:  # noqa: BLE001 — Redis 不可用不阻塞主流程
        _redis = None
    return _redis


def _memory_allow(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    now = int(time.time())
    with _lock:
        count, end = _memory.get(key, (0, 0))
        if end <= now:
            count, end = 0, now + window_s
        if count >= limit:
            return False, end - now
        _memory[key] = (count + 1, end)
        # 顺手清一下过期键，防止长期运行内存膨胀
        if len(_memory) > 4096:
            expired = [k for k, (_c, e) in _memory.items() if e <= now]
            for k in expired:
                _memory.pop(k, None)
        return True, 0


def allow(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """是否放行。返回 (allowed, retry_after_seconds)。"""
    if limit <= 0:      # 0 = 不限
        return True, 0
    client = _get_redis()
    if client is None:
        return _memory_allow(key, limit, window_s)
    try:
        import time as _t

        window = int(_t.time()) // window_s
        rkey = f"rl:{key}:{window}"
        pipe = client.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_s + 5)
        count = pipe.execute()[0]
        if count > limit:
            retry = window_s - (int(_t.time()) % window_s)
            return False, max(retry, 1)
        return True, 0
    except Exception:  # noqa: BLE001 — Redis 抖动回落内存
        return _memory_allow(key, limit, window_s)


# ---------------------------------------------------------------- 配额（按自然日）


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def quota_key(workspace_id: str, kind: str) -> str:
    return f"quota:{workspace_id}:{kind}:{_today()}"


def quota_usage(workspace_id: str, kind: str) -> int:
    """当前已用量（请求数或 Token 数）。"""
    client = _get_redis()
    if client is not None:
        try:
            return int(client.get(quota_key(workspace_id, kind)) or 0)
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        count, _end = _memory.get(quota_key(workspace_id, kind), (0, 0))
        return count


def quota_incr(workspace_id: str, kind: str, amount: int = 1) -> None:
    client = _get_redis()
    if client is not None:
        try:
            key = quota_key(workspace_id, kind)
            pipe = client.pipeline()
            pipe.incrby(key, amount)
            pipe.expire(key, 90000)  # 25h，跨过自然日重置
            pipe.execute()
            return
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        key = quota_key(workspace_id, kind)
        count, _end = _memory.get(key, (0, 0))
        _memory[key] = (count + amount, int(time.time()) + 90000)


def quota_check(workspace_id: str, kind: str) -> tuple[bool, int]:
    """是否还有当日配额。返回 (allowed, used)。limit=0 表示不限。"""
    settings = get_settings()
    limit = (settings.quota_daily_requests if kind == "requests"
             else settings.quota_daily_tokens)
    if limit <= 0:
        return True, 0
    used = quota_usage(workspace_id, kind)
    return used < limit, used


def record_tokens(amount: int) -> None:
    """llm 层调用：把本轮消耗的 Token 记到当前工作区的日配额上。"""
    if amount <= 0:
        return
    ws = current_ws.get()
    if ws:
        quota_incr(ws, "tokens", amount)
