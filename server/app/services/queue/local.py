"""本地队列：jobs 表 + 乐观锁 + 租约。

不需要额外中间件（Redis/RabbitMQ），复用已有的数据库连接，
适合单机开发与中小规模部署。多进程安全靠一条带条件的 UPDATE：

    UPDATE jobs SET status='running' ... WHERE id=? AND status='pending'

只有一个进程的 rowcount 会是 1，其余重试下一条。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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

JOB_COLUMNS = (
    "id, kind, status, payload, result, attempts, max_attempts, error,"
    " workspace_id, worker_id, lease_until, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deadline(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class LocalQueue:
    backend = "local"

    def __init__(self, db, *, lease_seconds: int = DEFAULT_LEASE_SECONDS,
                 worker_id: str | None = None) -> None:  # noqa: ANN001
        self.db = db
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"

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
        self.db.execute(
            "INSERT INTO jobs (id, kind, status, payload, result, attempts, max_attempts,"
            " error, workspace_id, worker_id, lease_until, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '{}', 0, ?, '', ?, '', '', ?, ?)",
            (job.id, job.kind, PENDING, dumps(job.payload), job.max_attempts,
             job.workspace_id, now, now),
        )
        return job

    # ------------------------------------------------------------ 领取
    def claim(self, block_seconds: int = 0) -> Job | None:
        self._recover_expired()
        # 一次多取几条候选：并发下大部分会被别的 worker 抢走，避免空转
        rows = self.db.query(
            "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 5", (PENDING,),
        )
        for row in rows:
            now = _now()
            affected = self.db.execute(
                "UPDATE jobs SET status = ?, attempts = attempts + 1, worker_id = ?,"
                " lease_until = ?, updated_at = ? WHERE id = ? AND status = ?",
                (RUNNING, self.worker_id, _deadline(self.lease_seconds), now, row["id"], PENDING),
            )
            if affected == 1:
                return self.get(row["id"])
        if block_seconds > 0:
            # 没有 BLPOP 可用，退化为一次短等待，让调用方的循环不至于打满 CPU
            import time

            time.sleep(min(block_seconds, 1.0))
        return None

    def renew(self, job_id: str) -> bool:
        affected = self.db.execute(
            "UPDATE jobs SET lease_until = ?, updated_at = ? WHERE id = ? AND status = ?",
            (_deadline(self.lease_seconds), _now(), job_id, RUNNING),
        )
        return affected == 1

    def _recover_expired(self) -> int:
        return max(self.db.execute(
            "UPDATE jobs SET status = ?, worker_id = '', lease_until = '', updated_at = ?"
            " WHERE status = ? AND lease_until <> '' AND lease_until < ?",
            (PENDING, _now(), RUNNING, _now()),
        ), 0)

    # ------------------------------------------------------------ 结束
    def complete(self, job_id: str, result: dict | None = None) -> Job | None:
        self.db.execute(
            "UPDATE jobs SET status = ?, result = ?, error = '', lease_until = '', updated_at = ?"
            " WHERE id = ?",
            (DONE, dumps(result), _now(), job_id),
        )
        return self.get(job_id)

    def fail(self, job_id: str, error: str) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        # attempts 在 claim 时已 +1，这里只判断是否还能再来一次
        status = PENDING if job.attempts < job.max_attempts else FAILED
        self.db.execute(
            "UPDATE jobs SET status = ?, error = ?, worker_id = '', lease_until = '', updated_at = ?"
            " WHERE id = ?",
            (status, error[:2000], _now(), job_id),
        )
        return self.get(job_id)

    def retry(self, job_id: str) -> Job | None:
        self.db.execute(
            "UPDATE jobs SET status = ?, attempts = 0, error = '', worker_id = '',"
            " lease_until = '', updated_at = ? WHERE id = ?",
            (PENDING, _now(), job_id),
        )
        return self.get(job_id)

    # ------------------------------------------------------------ 查询
    @staticmethod
    def _job(row: dict) -> Job:
        return Job(
            id=row["id"], kind=row["kind"], status=row["status"],
            payload=loads(row["payload"]), result=loads(row["result"]),
            attempts=int(row["attempts"] or 0), max_attempts=int(row["max_attempts"] or 1),
            error=row["error"] or "", workspace_id=row["workspace_id"] or "default",
            worker_id=row["worker_id"] or "", lease_until=row["lease_until"] or "",
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def get(self, job_id: str) -> Job | None:
        row = self.db.query_one(f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,))
        return self._job(row) if row else None

    def list(
        self, status: str | None = None, workspace_id: str | None = None, limit: int = 50,
    ) -> list[Job]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if workspace_id:
            where.append("workspace_id = ?")
            params.append(workspace_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        params.append(max(1, min(int(limit), 500)))
        rows = self.db.query(
            f"SELECT {JOB_COLUMNS} FROM jobs{clause} ORDER BY created_at DESC LIMIT ?", params,
        )
        return [self._job(r) for r in rows]

    def stats(self, workspace_id: str | None = None) -> dict[str, int]:
        if workspace_id:
            rows = self.db.query(
                "SELECT status, count(*) AS n FROM jobs WHERE workspace_id = ? GROUP BY status",
                (workspace_id,),
            )
        else:
            rows = self.db.query("SELECT status, count(*) AS n FROM jobs GROUP BY status")
        out = {s: 0 for s in (PENDING, RUNNING, DONE, FAILED)}
        for row in rows:
            out[row["status"]] = int(row["n"] or 0)
        return out

    def purge(self, days: int = 7) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 0))).isoformat()
        return max(self.db.execute(
            "DELETE FROM jobs WHERE status IN (?, ?) AND updated_at < ?",
            (DONE, FAILED, cutoff),
        ), 0)
