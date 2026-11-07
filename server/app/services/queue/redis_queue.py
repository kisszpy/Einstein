"""Redis 队列：list 做待执行、zset 做租约、hash 存任务体。

为什么三个结构：
- pending 用 list（RPUSH + BLPOP）→ 天然 FIFO，且 BLPOP 阻塞等待不空转
- processing 用 zset（score = 租约到期时间戳）→ 按时间范围一次捞出所有超时任务
- 任务体用 hash → 字段级更新，不必整条重写

多 worker 并发领取同一条是安全的：BLPOP 本身是原子弹出，
弹出后若 hash 已被删除（任务被清）就跳过继续下一条。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from app.services.queue.base import (
    DEFAULT_LEASE_SECONDS,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Job,
    dumps,
    loads,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deadline_epoch(seconds: int) -> float:
    return time.time() + seconds


def _block_timeout() -> tuple[type[BaseException], ...]:
    """BLPOP 空闲到期的异常类型。

    redis-py < 6 到期返回 None；>= 6 抛 redis.exceptions.TimeoutError，
    且它不继承内置 TimeoutError，必须单独捕获。redis 未安装时（注入 fake client）
    退化成内置 TimeoutError。
    """
    try:
        from redis.exceptions import TimeoutError as RedisTimeout
    except ImportError:  # pragma: no cover — 只有测试注入 client 时才会走到
        return (TimeoutError,)
    return (RedisTimeout, TimeoutError)


_BLOCK_TIMEOUT = _block_timeout()


class RedisQueue:
    backend = "redis"

    def __init__(
        self, url: str, *, name: str = "default",
        lease_seconds: int = DEFAULT_LEASE_SECONDS, worker_id: str | None = None,
        client=None,  # noqa: ANN001 — 测试可注入
    ) -> None:
        if client is not None:
            self.r = client
        else:
            import redis

            self.r = redis.Redis.from_url(url, decode_responses=True)
        self.name = name
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.prefix = f"einstein:q:{name}"

    # ---- 键 ----
    @property
    def k_pending(self) -> str:
        return f"{self.prefix}:pending"

    @property
    def k_processing(self) -> str:
        return f"{self.prefix}:processing"

    def k_job(self, job_id: str) -> str:
        return f"{self.prefix}:job:{job_id}"

    def ping(self) -> bool:
        return bool(self.r.ping())

    # ------------------------------------------------------------ 入队
    def enqueue(
        self, kind: str, payload: dict | None = None, *,
        workspace_id: str = "default", max_attempts: int = 3, job_id: str | None = None,
    ) -> Job:
        now = _now()
        job = Job(
            id=job_id or uuid.uuid4().hex[:16], kind=kind, payload=payload or {},
            workspace_id=workspace_id, max_attempts=max(1, int(max_attempts)),
            created_at=now, updated_at=now,
        )
        key = self.k_job(job.id)
        self.r.hset(key, mapping={
            "id": job.id, "kind": job.kind, "status": PENDING,
            "payload": dumps(job.payload), "result": "{}",
            "attempts": "0", "max_attempts": str(job.max_attempts), "error": "",
            "workspace_id": job.workspace_id, "worker_id": "", "lease_until": "",
            "created_at": now, "updated_at": now,
        })
        self.r.rpush(self.k_pending, job.id)
        return job

    # ------------------------------------------------------------ 领取
    def claim(self, block_seconds: float = 0) -> Job | None:
        self._recover_expired()
        if block_seconds > 0:
            try:
                item = self.r.blpop(self.k_pending, timeout=block_seconds)
            except _BLOCK_TIMEOUT:
                # redis-py 6+ 在 BLPOP 到期时抛异常而不是返回 None（老版本返回 None），
                # 语义上就是「这段时间没来任务」，交给调用方继续下一轮。
                return None
            job_id = item[1] if item else None
        else:
            job_id = self.r.lpop(self.k_pending)

        # 极端情况：弹出来的是已被清掉的任务，继续取，最多试几次避免死循环
        for _ in range(5):
            if not job_id:
                return None
            key = self.k_job(job_id)
            if not self.r.exists(key):
                job_id = self.r.lpop(self.k_pending)
                continue
            states = self.r.hgetall(key)
            if states.get("status") != PENDING:
                job_id = self.r.lpop(self.k_pending)
                continue
            attempts = int(states.get("attempts") or 0) + 1
            now = _now()
            deadline = _deadline_epoch(self.lease_seconds)
            self.r.hset(key, mapping={
                "status": RUNNING, "attempts": str(attempts), "worker_id": self.worker_id,
                "lease_until": datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
                "updated_at": now,
            })
            self.r.zadd(self.k_processing, {job_id: deadline})
            return self.get(job_id)
        return None

    def renew(self, job_id: str) -> bool:
        if not self.r.exists(self.k_job(job_id)):
            return False
        deadline = _deadline_epoch(self.lease_seconds)
        self.r.zadd(self.k_processing, {job_id: deadline})
        self.r.hset(self.k_job(job_id), mapping={
            "lease_until": datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
            "updated_at": _now(),
        })
        return True

    def _recover_expired(self) -> int:
        expired = self.r.zrangebyscore(self.k_processing, 0, time.time())
        recovered = 0
        for job_id in expired:
            # zrem 返回 1 才说明是我把这条从"处理中"摘下来的，避免多 worker 重复回收
            if not self.r.zrem(self.k_processing, job_id):
                continue
            key = self.k_job(job_id)
            if not self.r.exists(key):
                continue
            if self.r.hget(key, "status") != RUNNING:
                continue
            self.r.hset(key, mapping={
                "status": PENDING, "worker_id": "", "lease_until": "", "updated_at": _now(),
            })
            self.r.rpush(self.k_pending, job_id)
            recovered += 1
        return recovered

    # ------------------------------------------------------------ 结束
    def complete(self, job_id: str, result: dict | None = None) -> Job | None:
        key = self.k_job(job_id)
        if not self.r.exists(key):
            return None
        self.r.hset(key, mapping={
            "status": DONE, "result": dumps(result), "error": "",
            "lease_until": "", "updated_at": _now(),
        })
        self.r.zrem(self.k_processing, job_id)
        return self.get(job_id)

    def fail(self, job_id: str, error: str, *, permanent: bool = False) -> Job | None:
        key = self.k_job(job_id)
        if not self.r.exists(key):
            return None
        states = self.r.hgetall(key)
        attempts = int(states.get("attempts") or 0)
        max_attempts = int(states.get("max_attempts") or 1)
        retryable = not permanent and attempts < max_attempts
        status = PENDING if retryable else FAILED
        self.r.hset(key, mapping={
            "status": status, "error": (error or "")[:2000], "worker_id": "",
            "lease_until": "", "updated_at": _now(),
        })
        self.r.zrem(self.k_processing, job_id)
        if status == PENDING:
            self.r.rpush(self.k_pending, job_id)
        return self.get(job_id)

    def set_progress(self, job_id: str, progress: dict) -> bool:
        key = self.k_job(job_id)
        if not self.r.exists(key):
            return False
        self.r.hset(key, mapping={"progress": dumps(progress), "updated_at": _now()})
        return True

    def retry(self, job_id: str) -> Job | None:
        key = self.k_job(job_id)
        if not self.r.exists(key):
            return None
        self.r.hset(key, mapping={
            "status": PENDING, "attempts": "0", "error": "", "worker_id": "",
            "lease_until": "", "updated_at": _now(),
        })
        self.r.zrem(self.k_processing, job_id)
        self.r.rpush(self.k_pending, job_id)
        return self.get(job_id)

    # ------------------------------------------------------------ 查询
    def get(self, job_id: str) -> Job | None:
        states = self.r.hgetall(self.k_job(job_id))
        return self._job(states) if states else None

    @staticmethod
    def _job(states: dict) -> Job:
        return Job(
            id=states.get("id", ""), kind=states.get("kind", ""),
            status=states.get("status", PENDING),
            payload=loads(states.get("payload")), result=loads(states.get("result")),
            attempts=int(states.get("attempts") or 0),
            max_attempts=int(states.get("max_attempts") or 1),
            error=states.get("error", ""), workspace_id=states.get("workspace_id", "default"),
            worker_id=states.get("worker_id", ""), lease_until=states.get("lease_until", ""),
            created_at=states.get("created_at", ""), updated_at=states.get("updated_at", ""),
            progress=loads(states.get("progress")),
        )

    def list(
        self, status: str | None = None, workspace_id: str | None = None, limit: int = 50,
    ) -> list[Job]:
        jobs: list[Job] = []
        pattern = f"{self.prefix}:job:*"
        for key in self.r.scan_iter(match=pattern, count=200):
            states = self.r.hgetall(key)
            if not states:
                continue
            if status and states.get("status") != status:
                continue
            if workspace_id and states.get("workspace_id") != workspace_id:
                continue
            jobs.append(self._job(states))
            if len(jobs) >= max(1, min(int(limit), 500)) * 4:
                break
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[: max(1, min(int(limit), 500))]

    def stats(self, workspace_id: str | None = None) -> dict[str, int]:
        out = {s: 0 for s in (PENDING, RUNNING, DONE, FAILED)}
        for job in self.list(status=None, workspace_id=workspace_id, limit=500):
            out[job.status] = out.get(job.status, 0) + 1
        return out

    def purge(self, days: int = 7) -> int:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 0))).isoformat()
        removed = 0
        for key in self.r.scan_iter(match=f"{self.prefix}:job:*", count=200):
            states = self.r.hgetall(key)
            if states.get("status") in (DONE, FAILED) and states.get("updated_at", "") < cutoff:
                self.r.delete(key)
                removed += 1
        return removed
