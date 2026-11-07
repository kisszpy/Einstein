"""队列工厂。

`queue_backend` 决定实现：
- local（默认）：jobs 表 + 乐观锁 + 租约，零额外中间件
- redis：list/zset/hash，支持多机 worker 与阻塞取出

两种实现的 Job 结构与状态机完全一致，切换后端不需要改业务代码。
"""

from __future__ import annotations

import threading

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.schema import migrate
from app.services.queue.base import (
    DEFAULT_LEASE_SECONDS,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Job,
    JobQueue,
)
from app.services.queue.local import LocalQueue
from app.services.queue.redis_queue import RedisQueue

__all__ = [
    "DONE",
    "FAILED",
    "Job",
    "JobQueue",
    "LocalQueue",
    "PENDING",
    "RUNNING",
    "RedisQueue",
    "get_queue",
    "reset_queue",
]

_queues: dict[str, JobQueue] = {}
_lock = threading.Lock()


def get_queue(name: str = "default") -> JobQueue:
    with _lock:
        if name not in _queues:
            s = get_settings()
            if s.queue_backend == "redis":
                _queues[name] = RedisQueue(
                    s.redis_url, name=name, lease_seconds=DEFAULT_LEASE_SECONDS,
                )
            else:
                db = get_db()
                migrate(db)
                _queues[name] = LocalQueue(db, lease_seconds=DEFAULT_LEASE_SECONDS)
        return _queues[name]


def reset_queue() -> None:
    """测试用：换后端后重新构建。"""
    with _lock:
        _queues.clear()
