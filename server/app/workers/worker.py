"""任务队列 worker：独立进程跑，不占用 Web 进程的线程/事件循环。

用法：
    python -m app.workers.worker --once                    # 取一条跑完即退（自检/CI）
    python -m app.workers.worker --concurrency 2           # 常驻，2 个并发槽
    python -m app.workers.worker --backend redis --block   # Redis：阻塞等待，不空转

设计要点：
1. 执行中后台线程定期续租 —— 长任务（批量嵌入）不会因为超过租约被别的 worker 抢走
2. handler 抛异常 → fail()，队列按 attempts/max_attempts 决定重试或 failed
3. 未知 kind 直接标记 failed，不重试（重试一万次也没有 handler）
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.queue import Job, get_queue  # noqa: E402
from app.services.queue.handlers import HANDLERS  # noqa: E402


class LeaseKeeper:
    """执行期间续租，避免长任务被当成僵尸回收。"""

    def __init__(self, queue, job_id: str, interval: float = 60.0) -> None:  # noqa: ANN001
        self.queue = queue
        self.job_id = job_id
        self.interval = max(interval, 1.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "LeaseKeeper":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.queue.renew(self.job_id)
            except Exception:  # noqa: BLE001 — 续租失败不致命，交给租约回收
                pass


def execute(queue, job: Job) -> bool:  # noqa: ANN001
    handler = HANDLERS.get(job.kind)
    if handler is None:
        queue.fail(job.id, f"未知任务类型：{job.kind}")
        print(f"[worker] {job.id} 未知 kind={job.kind}，标记 failed", flush=True)
        return False

    t0 = time.perf_counter()
    print(f"[worker] {job.id} 开始 kind={job.kind} attempt={job.attempts}/{job.max_attempts}", flush=True)
    try:
        with LeaseKeeper(queue, job.id):
            result = handler(job) or {}
        queue.complete(job.id, result)
        ms = (time.perf_counter() - t0) * 1000
        print(f"[worker] {job.id} 完成 {ms:.0f}ms · {result}", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001 — 任何异常都转成任务失败，worker 不退出
        detail = f"{type(exc).__name__}: {exc}"
        updated = queue.fail(job.id, detail)
        status = updated.status if updated else "?"
        print(f"[worker] {job.id} 失败 → {status}：{detail}", flush=True)
        traceback.print_exc()
        return False


def run_once(queue) -> bool:  # noqa: ANN001
    job = queue.claim()
    if job is None:
        return False
    execute(queue, job)
    return True


def run_forever(queue, *, concurrency: int, poll: float, block: bool, max_jobs: int = 0) -> int:  # noqa: ANN001
    stop = threading.Event()
    counter = {"done": 0}
    lock = threading.Lock()

    def _handle_signal(signum, _frame) -> None:  # noqa: ANN001
        print(f"[worker] 收到信号 {signum}，等待当前任务结束…", flush=True)
        stop.set()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except ValueError:  # 非主线程（测试）无法注册信号
        pass

    def slot(idx: int) -> None:
        while not stop.is_set():
            job = queue.claim(block_seconds=int(poll) if block else 0)
            if job is None:
                if block:
                    continue
                stop.wait(poll)
                continue
            execute(queue, job)
            with lock:
                counter["done"] += 1
                if max_jobs and counter["done"] >= max_jobs:
                    stop.set()

    threads = [threading.Thread(target=slot, args=(i,), daemon=True) for i in range(max(1, concurrency))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return counter["done"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default="default")
    parser.add_argument("--backend", choices=["local", "redis"], default=None)
    parser.add_argument("--once", action="store_true", help="只处理一条任务后退出")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--poll", type=float, default=2.0, help="空队列轮询间隔（秒）")
    parser.add_argument("--block", action="store_true", help="Redis 后端阻塞等待")
    parser.add_argument("--max-jobs", type=int, default=0)
    args = parser.parse_args()

    if args.backend:
        # 让 --backend 覆盖配置，便于本地临时切 Redis 验证
        import os

        os.environ["APP_QUEUE_BACKEND"] = args.backend
        from app.core.config import get_settings

        get_settings.cache_clear()
        from app.services.queue import reset_queue

        reset_queue()

    queue = get_queue(args.queue)
    print(f"[worker] queue={args.queue} backend={queue.backend} pid={__import__('os').getpid()}", flush=True)

    if args.once:
        handled = run_once(queue)
        print("[worker] 无待执行任务" if not handled else "[worker] 已处理 1 条")
        return 0

    done = run_forever(
        queue, concurrency=args.concurrency, poll=args.poll,
        block=args.block, max_jobs=args.max_jobs,
    )
    print(f"[worker] 退出，共处理 {done} 条", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
