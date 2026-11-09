"""队列归属：过期租约被重领后，旧 worker 不许再写结果。

这是"任务明明还在跑，状态却突然变成 done"的根因：租约 300 秒，长任务
（批量嵌入）在旧 worker 里还没结束，租约先过期、任务被新 worker 接手，
旧 worker 醒来把自己的结果一写，账面就乱了——新 worker 还在写同一批切片。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.engine import get_db
from app.db.schema import migrate
from app.services.queue.base import DONE, RUNNING
from app.services.queue.local import LocalQueue


@pytest.fixture()
def queue():
    db = get_db()
    migrate(db)
    q = LocalQueue(db, lease_seconds=60)
    yield q


def _expire(q: LocalQueue, job_id: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    q.db.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (past, job_id))


def test_stale_worker_cannot_complete_after_reclaim(queue):
    job = queue.enqueue("noop", {"n": 1}, workspace_id="ws-q")
    first = queue.claim()
    assert first is not None and first.id == job.id

    # 另一个 worker 接手：租约过期 → 被重领
    _expire(queue, job.id)
    other = LocalQueue(get_db(), lease_seconds=60)
    taken = other.claim()
    assert taken is not None and taken.id == job.id
    assert taken.worker_id != queue.worker_id

    # 旧 worker 想把"我完成了"写进去——必须被拒
    assert queue.complete(job.id, {"stale": True}) is None
    assert queue.get(job.id).status == RUNNING

    # 新 worker 才能收尾
    done = other.complete(job.id, {"ok": True})
    assert done is not None and done.status == DONE
    assert done.result.get("ok") is True


def test_stale_worker_cannot_fail_after_reclaim(queue):
    job = queue.enqueue("noop", {}, workspace_id="ws-q")
    queue.claim()
    _expire(queue, job.id)
    other = LocalQueue(get_db(), lease_seconds=60)
    other.claim()

    assert queue.fail(job.id, "旧 worker 的报告") is None
    assert queue.get(job.id).error == ""


def test_progress_from_api_is_still_allowed(queue):
    """进度条是接口进程写的（不是 worker），不能被归属校验挡掉。"""
    job = queue.enqueue("noop", {}, workspace_id="ws-q")
    queue.claim()
    assert queue.set_progress(job.id, {"stage": "解析", "percent": 10}) is True
