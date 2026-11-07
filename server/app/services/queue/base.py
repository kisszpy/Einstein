"""异步任务队列协议。

为什么需要队列：知识库批量入库、批量嵌入、索引重建都是分钟级操作，
不能挂在 HTTP 请求里（超时 + 用户刷新就丢）。队列保证三件事：
1. 任务落库/落 Redis，进程重启不丢
2. 至少执行一次（失败自动重试，超过上限标记 failed）
3. 同一任务不会被两个 worker 同时处理（乐观锁 + 租约）

状态机：pending → running → done
                        ↘ pending（失败且未超上限）
                        ↘ failed（超过 max_attempts）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STATUSES = (PENDING, RUNNING, DONE, FAILED)

DEFAULT_LEASE_SECONDS = 300


class PermanentJobError(RuntimeError):
    """不该重试的失败（格式不支持、文件损坏、越权）。worker 直接标记 failed。"""


@dataclass
class Job:
    id: str
    kind: str
    status: str = PENDING
    payload: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    error: str = ""
    workspace_id: str = "default"
    worker_id: str = ""
    lease_until: str = ""
    created_at: str = ""
    updated_at: str = ""
    progress: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "payload": self.payload, "result": self.result, "attempts": self.attempts,
            "max_attempts": self.max_attempts, "error": self.error,
            "workspace_id": self.workspace_id, "created_at": self.created_at,
            "updated_at": self.updated_at, "progress": self.progress,
        }


class JobQueue(Protocol):
    backend: str

    def enqueue(
        self, kind: str, payload: dict | None = None, *,
        workspace_id: str = "default", max_attempts: int = 3, job_id: str | None = None,
    ) -> Job: ...

    def claim(self, block_seconds: int = 0) -> Job | None:
        """取一条待执行任务并标记 running；没有任务返回 None。

        block_seconds > 0 时允许实现阻塞等待（Redis 用 BLPOP，本地实现退化为短轮询）。
        """
        ...

    def renew(self, job_id: str) -> bool:
        """续租：长任务执行期间调用，避免被当成僵尸任务回收。"""
        ...

    def complete(self, job_id: str, result: dict | None = None) -> Job | None: ...

    def fail(self, job_id: str, error: str, *, permanent: bool = False) -> Job | None: ...

    def set_progress(self, job_id: str, progress: dict) -> bool:
        """记录执行进度（阶段 / 百分比 / 已完成数量），供前端轮询。"""
        ...

    def get(self, job_id: str) -> Job | None: ...

    def list(
        self, status: str | None = None, workspace_id: str | None = None, limit: int = 50,
    ) -> list[Job]: ...

    def retry(self, job_id: str) -> Job | None: ...

    def purge(self, days: int = 7) -> int: ...


def dumps(value: dict | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def loads(raw: str | bytes | None) -> dict:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
    except Exception:  # noqa: BLE001 — 脏数据不该让任务列表整体 500
        return {}
    return value if isinstance(value, dict) else {}
