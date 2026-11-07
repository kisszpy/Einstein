"""任务队列验收：local（jobs 表）与 redis 两种后端跑同一套语义。

验收点（这是后面批量嵌入/索引重建能安全上线的门槛）：
1. 入队 → 领取 → 完成，payload/result 往返不丢
2. 并发领取不重复执行（10 个 worker 抢 1 条，只能有 1 个成功）
3. 失败重试：未超上限回 pending，超上限标 failed
4. 租约回收：worker 崩溃留下的 running 任务会被重新领取
5. 状态过滤、按工作区隔离、retry 重置计数
6. purge 只清历史终态任务，不碰 pending/running

用法：
    ./.venv/Scripts/python.exe tools/verify_queue.py
    ./.venv/Scripts/python.exe tools/verify_queue.py --only redis --verbose
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.engine import Database  # noqa: E402
from app.db.schema import migrate  # noqa: E402
from app.services.queue.base import DONE, FAILED, PENDING, RUNNING  # noqa: E402
from app.services.queue.local import LocalQueue  # noqa: E402
from app.services.queue.redis_queue import RedisQueue  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []


def check(backend: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((backend, name, ok, detail))
    print(f"{'OK  ' if ok else 'FAIL'} [{backend}] {name}" + (f" · {detail}" if detail else ""))


def run_suite(backend: str, make_queue) -> None:  # noqa: ANN001
    queue = make_queue()

    # ---- 入队 ----
    job = queue.enqueue("noop", {"n": 1, "text": "死锁"}, workspace_id="ws-a")
    got = queue.get(job.id)
    check(backend, "入队状态为 pending", got is not None and got.status == PENDING)
    check(backend, "payload 往返", got is not None and got.payload == {"n": 1, "text": "死锁"})

    # ---- 领取 ----
    claimed = queue.claim()
    check(backend, "领取后 running", claimed is not None and claimed.status == RUNNING,
          f"attempts={claimed.attempts if claimed else 'NA'}")
    check(backend, "领取时计数 +1", claimed is not None and claimed.attempts == 1)
    check(backend, "领取带 worker_id", bool(claimed and claimed.worker_id))

    # ---- 完成 ----
    done = queue.complete(job.id, {"ok": True, "n": 42})
    check(backend, "完成后 done", done is not None and done.status == DONE)
    check(backend, "result 往返", done is not None and done.result.get("n") == 42)

    # ---- 并发领取：只能有一个赢 ----
    solo = queue.enqueue("noop", {"single": True}, workspace_id="ws-a")
    winners: list[str] = []
    lock = threading.Lock()

    def grab() -> None:
        q = make_queue()
        item = q.claim()
        if item is not None:
            with lock:
                winners.append(item.id)

    threads = [threading.Thread(target=grab) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(backend, "并发 10 抢 1 只有 1 个成功",
          len(winners) == 1 and winners[0] == solo.id, f"winners={winners}")
    queue.complete(solo.id, {})

    # ---- 失败重试 ----
    flaky = queue.enqueue("noop", {"f": 1}, workspace_id="ws-a", max_attempts=2)
    queue.claim()
    after_fail = queue.fail(flaky.id, "第一次失败")
    check(backend, "未超上限回 pending",
          after_fail is not None and after_fail.status == PENDING and after_fail.attempts == 1,
          f"status={after_fail.status if after_fail else 'NA'}")
    queue.claim()
    final = queue.fail(flaky.id, "第二次失败")
    check(backend, "超上限标 failed", final is not None and final.status == FAILED,
          f"status={final.status if final else 'NA'}")
    check(backend, "失败原因留存", bool(final and "第二次失败" in final.error))

    # ---- 手动重试 ----
    again = queue.retry(flaky.id)
    check(backend, "retry 重置为 pending 且计数归零",
          again is not None and again.status == PENDING and again.attempts == 0)
    # 把这条消费掉，免得后面的领取断言拿到它
    queue.claim()
    queue.complete(flaky.id, {})

    # ---- 租约回收（模拟 worker 崩溃）----
    zombie = queue.enqueue("noop", {"z": 1}, workspace_id="ws-a")
    queue.claim()
    _expire_lease(queue, zombie.id)
    recovered = queue.claim()
    check(backend, "过期租约被回收重领",
          recovered is not None and recovered.id == zombie.id,
          f"id={recovered.id if recovered else 'NA'}")
    queue.complete(zombie.id, {})

    # ---- 续租 ----
    keeper = queue.enqueue("noop", {"keep": 1}, workspace_id="ws-a")
    queue.claim()
    ok_renew = queue.renew(keeper.id)
    check(backend, "续租成功", bool(ok_renew) and queue.get(keeper.id).status == RUNNING)
    queue.complete(keeper.id, {})

    # ---- 列表与隔离 ----
    listed = queue.list(status=DONE, workspace_id="ws-a")
    check(backend, "按状态过滤", all(j.status == DONE for j in listed) and len(listed) >= 1,
          f"{len(listed)} 条")
    other = queue.enqueue("noop", {}, workspace_id="ws-b")
    check(backend, "工作区隔离",
          all(j.id != other.id for j in queue.list(workspace_id="ws-a")))

    # ---- 未知 kind 由 worker 兜底（这里只验队列本身不拒绝）----
    weird = queue.enqueue("not-a-real-kind", {})
    check(backend, "未知 kind 可入队（由 worker 标记 failed）", queue.get(weird.id) is not None)

    # ---- 空队列阻塞领取 ----
    # redis-py 6+ 在 BLPOP 到期时抛 TimeoutError 而不是返回 None，且该异常不继承内置
    # TimeoutError。没兜住的话 worker 的领取线程会直接死掉，整个进程静默退出（exit 0）。
    if backend == "redis":
        idle = RedisQueue("", name=f"idle-{uuid.uuid4().hex[:6]}", client=queue.r)
        t0 = time.monotonic()
        try:
            got = idle.claim(block_seconds=1)
            raised = ""
        except Exception as exc:  # noqa: BLE001
            got, raised = None, f"{type(exc).__name__}: {exc}"
        spent = time.monotonic() - t0
        check(backend, "空队列阻塞领取返回 None（不抛异常）",
              not raised and got is None and 0.8 <= spent <= 6,
              f"{spent:.1f}s{(' ' + raised) if raised else ''}")

    # ---- purge ----
    keep = queue.enqueue("noop", {"keep_pending": True})
    _age(queue, done.id if done else job.id, days=30)
    removed = queue.purge(days=7)
    check(backend, "purge 清理历史终态", removed >= 1, f"删除 {removed} 条")
    check(backend, "purge 不碰 pending",
          queue.get(keep.id) is not None and queue.get(keep.id).status == PENDING)


def _expire_lease(queue, job_id: str) -> None:
    """把租约改成过去时间，模拟 worker 崩溃。"""
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    if isinstance(queue, LocalQueue):
        queue.db.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (past, job_id))
    else:
        queue.r.zadd(queue.k_processing, {job_id: time.time() - 5})
        queue.r.hset(queue.k_job(job_id), "lease_until", past)


def _age(queue, job_id: str, days: int) -> None:
    """把任务的 updated_at 改老，验证 purge 的时间窗口。"""
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if isinstance(queue, LocalQueue):
        queue.db.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old, job_id))
    else:
        queue.r.hset(queue.k_job(job_id), "updated_at", old)


class LocalFactory:
    """每次调用返回一个新连接（模拟多个 worker 进程），库文件只建一次。"""

    def __init__(self, tag: str) -> None:
        self.path = Path(tempfile.gettempdir()) / f"einstein_verify_queue_{tag}.db"
        if self.path.exists():
            self.path.unlink()
        db = Database("sqlite", str(self.path))
        migrate(db)
        db.close()

    def __call__(self) -> LocalQueue:
        return LocalQueue(Database("sqlite", str(self.path)))


def build_redis(url: str, name: str) -> RedisQueue:
    return RedisQueue(url, name=name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["local", "redis"], default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings

    s = get_settings()
    targets = [args.only] if args.only else ["local", "redis"]

    for backend in targets:
        tag = f"{backend}-{uuid.uuid4().hex[:6]}"
        try:
            if backend == "local":
                run_suite("local", LocalFactory(tag))
            else:
                name = f"verify-{tag}"
                client = build_redis(s.redis_url, name)
                run_suite("redis", lambda: build_redis(s.redis_url, name))
                for key in client.r.scan_iter(match=f"{client.prefix}:*"):
                    client.r.delete(key)
            print()
        except Exception as exc:  # noqa: BLE001
            check(backend, "套件执行", False, f"{type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[2]]
    print("-" * 70)
    print(f"结果：{total - len(failed)}/{total} 通过")
    for backend, name, _ok, detail in failed:
        print(f"  失败 [{backend}] {name} · {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
