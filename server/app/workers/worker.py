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
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.services.queue import Job, get_queue  # noqa: E402
from app.services.queue.base import PermanentJobError  # noqa: E402
from app.services.queue.handlers import HANDLERS  # noqa: E402

logger = get_logger("app.worker")


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
                # 掉到 debug：长任务每 60s 续一次，队列抖动时这条会刷屏
                logger.debug("续租失败 job=%s（等租约自然回收）", self.job_id, exc_info=True)


def execute(queue, job: Job) -> bool:  # noqa: ANN001
    handler = HANDLERS.get(job.kind)
    if handler is None:
        queue.fail(job.id, f"未知任务类型：{job.kind}")
        logger.error("任务 %s 的 kind=%s 没有对应 handler，已标记 failed", job.id, job.kind)
        return False

    t0 = time.perf_counter()
    logger.info(
        "任务开始 %s kind=%s attempt=%d/%d",
        job.id, job.kind, job.attempts, job.max_attempts,
    )
    try:
        with LeaseKeeper(queue, job.id):
            result = handler(job) or {}
        queue.complete(job.id, result)
        ms = (time.perf_counter() - t0) * 1000
        logger.info("任务完成 %s %.0fms · %s", job.id, ms, result)
        return True
    except Exception as exc:  # noqa: BLE001 — 任何异常都转成任务失败，worker 不退出
        detail = f"{type(exc).__name__}: {exc}"
        # 格式不支持/文件损坏这类问题重试多少次都一样，直接判死
        updated = queue.fail(job.id, detail, permanent=isinstance(exc, PermanentJobError))
        status = updated.status if updated else "?"
        # 带 traceback：失败任务是排查的主要入口，只留一行 summary 没法定位
        logger.exception("任务失败 %s → %s：%s", job.id, status, detail)
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
        logger.info("收到信号 %s，等待当前任务结束…", signum)
        stop.set()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except ValueError:  # 非主线程（测试）无法注册信号
        pass

    def slot(idx: int) -> None:
        # BLPOP 的 timeout 取整秒；小于 1 会让阻塞变成非阻塞，进而空转烧 CPU
        block_seconds = max(1, int(round(poll))) if block else 0
        backoff = 1.0
        while not stop.is_set():
            started = time.monotonic()
            try:
                job = queue.claim(block_seconds=block_seconds)
            except Exception as exc:  # noqa: BLE001 — 队列抖动（网络/空闲超时）不该杀掉 worker
                logger.warning(
                    "槽 %d 领取任务失败（%s: %s），%.0fs 后重试",
                    idx, type(exc).__name__, exc, backoff,
                )
                stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            if job is None:
                if block:
                    # 正常 BLPOP 会整整阻塞 block_seconds；若明显提前返回（连接抖动、
                    # 队列实现不阻塞），补一个短等待，否则这里会变成空转烧 CPU。
                    if time.monotonic() - started < block_seconds * 0.5:
                        stop.wait(min(1.0, max(poll, 0.2)))
                    continue
                stop.wait(poll)
                continue
            # 单条任务出任何状况都不能让槽退出：槽悄悄少一个，吞吐就掉了，
            # 而且外层 join 一返回整个 worker 就以 exit 0 结束，没人会发现。
            try:
                execute(queue, job)
            except SystemExit as exc:
                logger.warning("槽 %d 被中断（%s），继续后续任务", idx, exc)
                continue
            except BaseException as exc:  # noqa: BLE001 — 兜底，保证槽存活
                logger.error("槽 %d 执行异常（%s: %s），继续", idx, type(exc).__name__, exc)
                continue
            with lock:
                counter["done"] += 1
                if max_jobs and counter["done"] >= max_jobs:
                    stop.set()

    threads = [threading.Thread(target=slot, args=(i,), daemon=True) for i in range(max(1, concurrency))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if not stop.is_set():
        # 走到这里说明所有槽都意外结束了但没人要求停：如实报出来，别静默退出
        logger.error("全部槽已退出，但未收到停止信号，可能是环境拦截了文件操作")
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

    # worker 是独立进程，日志要自己初始化一遍（不经过 create_app）
    setup_logging()

    if args.backend:
        # 让 --backend 覆盖配置，便于本地临时切 Redis 验证
        os.environ["APP_QUEUE_BACKEND"] = args.backend
        from app.core.config import get_settings

        get_settings.cache_clear()
        from app.services.queue import reset_queue

        reset_queue()

    queue = get_queue(args.queue)
    logger.info(
        "worker 启动 queue=%s backend=%s pid=%d concurrency=%d",
        args.queue, queue.backend, os.getpid(), args.concurrency,
    )

    if args.once:
        handled = run_once(queue)
        logger.info("已处理 1 条" if handled else "无待执行任务")
        return 0

    done = run_forever(
        queue, concurrency=args.concurrency, poll=args.poll,
        block=args.block, max_jobs=args.max_jobs,
    )
    logger.info("worker 退出，共处理 %d 条", done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
