"""Redis 队列：list 做待执行、zset 做租约、hash 存任务体。

为什么三个结构：
- pending 用 list（RPUSH + BLPOP）→ 天然 FIFO，且 BLPOP 阻塞等待不空转
- processing 用 zset（score = 租约到期时间戳）→ 按时间范围一次捞出所有超时任务
- 任务体用 hash → 字段级更新，不必整条重写

**领取为什么不是"BLPOP 完再改状态"**：BLPOP 一返回，任务就已经从 pending 里
消失了。如果进程此刻被 kill（部署、OOM、Ctrl+C），这条任务既不在 pending、
也不在 processing——它凭空消失了，而账面上它还是 pending。所以领取分两步且
两步都可恢复：先把 id 原子搬到 inflight 列表（BLMOVE / LMOVE），再用一段 Lua
把它标成 running 并进 zset、从 inflight 摘掉。中间任意时刻崩溃，inflight 里
都留着这条 id，`_recover_expired` 会把它放回 pending。

多 worker 并发领取同一条是安全的：弹出本身原子，标记阶段用 HGET status 判定，
只有还是 pending 的那一个能改成 running。
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

    @property
    def k_inflight(self) -> str:
        """领取途中（已离开 pending、还没标成 running）的暂存区。

        它存在的唯一理由是"崩溃后还能找回来"。正常情况下一条 id 在这里停留
        几毫秒。
        """
        return f"{self.prefix}:inflight"

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
    # 标记阶段整段在 Redis 里执行：检查状态 → attempts+1 → running → 进 zset →
    # 从 inflight 摘掉，中间不可能被别的命令插队。
    _MARK_LUA = """
    local key = KEYS[1]
    local inflight = KEYS[2]
    local processing = KEYS[3]
    local jid = ARGV[1]
    redis.call('LREM', inflight, 1, jid)
    if redis.call('EXISTS', key) == 0 then return 0 end
    if redis.call('HGET', key, 'status') ~= 'pending' then return 0 end
    redis.call('HINCRBY', key, 'attempts', 1)
    redis.call('HSET', key, 'status', 'running', 'worker_id', ARGV[2],
               'lease_until', ARGV[4], 'updated_at', ARGV[4])
    redis.call('ZADD', processing, tonumber(ARGV[3]), jid)
    return 1
    """

    def claim(self, block_seconds: float = 0) -> Job | None:
        self._recover_expired()
        # 最多试 5 次：弹出的是"已删除/已被别人领走"的残影时继续下一条
        for _ in range(5):
            job_id = self._take(block_seconds)
            block_seconds = 0   # 只有第一次值得阻塞，后面再等就是空转
            if not job_id:
                return None
            if self._mark_running(job_id):
                return self.get(job_id)
        return None

    def _take(self, block_seconds: float) -> str | None:
        """把一条待执行任务原子地搬到 inflight，返回 id。"""
        try:
            if block_seconds > 0:
                return self.r.blmove(
                    self.k_pending, self.k_inflight, block_seconds, "LEFT", "RIGHT",
                )
            return self.r.lmove(self.k_pending, self.k_inflight, "LEFT", "RIGHT")
        except _BLOCK_TIMEOUT:
            # 阻塞到期：这段时间没来任务，交给调用方继续下一轮
            return None
        except Exception as exc:  # noqa: BLE001
            # Redis < 6.2 没有 LMOVE/BLMOVE。退回旧写法，代价是"弹出后、标记前"
            # 崩溃会丢这一条——比整个队列不可用要好，但要让人看得见。
            if "unknown command" not in str(exc).lower():
                raise
            print(
                "[queue] Redis 版本过低（无 LMOVE），领取降级为 BLPOP；"
                "崩溃窗口内可能丢任务，建议升级到 6.2+", flush=True,
            )
            if block_seconds > 0:
                try:
                    item = self.r.blpop(self.k_pending, timeout=block_seconds)
                except _BLOCK_TIMEOUT:
                    return None
                return item[1] if item else None
            return self.r.lpop(self.k_pending)

    def _mark_running(self, job_id: str) -> bool:
        key = self.k_job(job_id)
        try:
            result = self.r.eval(
                self._MARK_LUA, 3, key, self.k_inflight, self.k_processing,
                job_id, self.worker_id,
                _deadline_epoch(self.lease_seconds), _now(),
            )
            return bool(result)
        except Exception:  # noqa: BLE001 — 不支持 EVAL 的客户端退化为逐条写
            self.r.lrem(self.k_inflight, 1, job_id)
            if not self.r.exists(key):
                return False
            if self.r.hget(key, "status") != PENDING:
                return False
            self.r.hset(key, mapping={
                "status": RUNNING,
                "attempts": str(int(self.r.hget(key, "attempts") or 0) + 1),
                "worker_id": self.worker_id,
                "lease_until": _now(), "updated_at": _now(),
            })
            self.r.zadd(self.k_processing, {job_id: _deadline_epoch(self.lease_seconds)})
            return True

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
        # 先捞"领取途中崩掉"的残影：它们既不在 pending 也不在 zset 里，
        # 只能从 inflight 找回来。正常情况这里几乎是空的（停留几毫秒）。
        rescued = 0
        for _ in range(100):
            job_id = self.r.lpop(self.k_inflight)
            if not job_id:
                break
            key = self.k_job(job_id)
            if self.r.exists(key) and self.r.hget(key, "status") == PENDING:
                self.r.rpush(self.k_pending, job_id)
                rescued += 1

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
        return recovered + rescued

    # ------------------------------------------------------------ 结束
    def _owned(self, job_id: str) -> bool:
        """这条任务现在是不是归我执行。

        租约过期后任务可能已被别的 worker 重领，此时旧 worker 若还去写
        complete/fail，就会把别人的执行结果覆盖掉——表现是"任务明明还在跑，
        状态却变成 done/failed"。所以结束类写入必须校验归属。
        """
        return (self.r.hget(self.k_job(job_id), "worker_id") or "") == self.worker_id

    def complete(self, job_id: str, result: dict | None = None) -> Job | None:
        key = self.k_job(job_id)
        if not self.r.exists(key):
            return None
        if not self._owned(job_id):
            print(f"[queue] {job_id} 已被其它 worker 接手，忽略本次 complete", flush=True)
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
        if not self._owned(job_id):
            print(f"[queue] {job_id} 已被其它 worker 接手，忽略本次 fail", flush=True)
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
