"""运行登记簿：一条任务的执行与「谁在看」彻底解耦。

**为什么需要**：FastAPI 的 `StreamingResponse` 在客户端断开时会取消生成器。
旧实现把执行写在生成器里，于是浏览器切走标签页、网络闪断、用户点「停止」，
都会连带把任务掐掉——产物可能已经写了一半，运行记录却查不到，用户回来
只看到「什么都没有」。这里把执行放进独立 task，事件写进登记簿；SSE 只是
登记簿的一个读者，断开不影响执行，重连还能从断点继续看。

代价是事件会在内存里留一段时间（默认 30 分钟），所以有 TTL 清理：任务
结束且超过保留期就整条丢掉。这跟「运行历史」是两件事——历史落库，
登记簿只服务于「这次运行还没看完」这一段。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

# 结束后的保留时长：够用户切回来、刷新、或换个设备重连
RETENTION_SECONDS = 1800
# 单条运行最多留多少帧，防止长任务把内存吃穿（超出丢弃最早的步骤帧）
MAX_EVENTS = 4000

_RUNS: dict[str, RunRecord] = {}
_TASKS: dict[str, asyncio.Task] = {}


@dataclass
class RunRecord:
    """一次运行的只增事件日志 + 唤醒信号。"""

    run_id: str
    session_id: str
    workspace_id: str
    owner_id: str
    events: list[tuple[str, dict]] = field(default_factory=list)
    done: bool = False
    failed: bool = False
    created_at: float = field(default_factory=time.monotonic)
    # 消费者用它等待「有新事件了」；push 时 set，消费完 clear
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, event: str, data: dict) -> None:
        if len(self.events) >= MAX_EVENTS:
            # 极端长任务（上万次工具调用）才会触发；丢最早的帧保住最新进展
            self.events = self.events[-MAX_EVENTS:]
        self.events.append((event, data))
        self.changed.set()

    def finish(self, *, failed: bool = False) -> None:
        self.done = True
        self.failed = failed
        self.changed.set()


def create(*, session_id: str, workspace_id: str, owner_id: str) -> RunRecord:
    """登记一次新运行。run_id 与 session_id 不同：一个会话可以有多轮运行。"""
    purge()
    record = RunRecord(
        run_id=uuid.uuid4().hex,
        session_id=session_id,
        workspace_id=workspace_id,
        owner_id=owner_id,
    )
    _RUNS[record.run_id] = record
    return record


def get(run_id: str, *, workspace_id: str, owner_id: str) -> RunRecord | None:
    """按归属取回运行：别人的 run_id 一律当作不存在（不泄露存在性）。"""
    record = _RUNS.get(run_id)
    if record is None:
        return None
    if record.workspace_id != workspace_id or record.owner_id != owner_id:
        return None
    return record


def latest_for_session(session_id: str, *, workspace_id: str, owner_id: str) -> RunRecord | None:
    """同会话最近一次运行：前端刷新后靠它接回正在跑的那一轮。"""
    for record in reversed(list(_RUNS.values())):
        if (
            record.session_id == session_id
            and record.workspace_id == workspace_id
            and record.owner_id == owner_id
        ):
            return record
    return None


def attach_task(run_id: str, task: asyncio.Task) -> None:
    """持有后台任务的强引用：事件循环只持弱引用，否则任务可能被 GC 掉。"""
    _TASKS[run_id] = task
    task.add_done_callback(lambda _t: _TASKS.pop(run_id, None))


def purge() -> int:
    """清理结束且超过保留期的运行，返回清理条数。"""
    now = time.monotonic()
    dead = [
        rid for rid, rec in _RUNS.items()
        if rec.done and (now - rec.created_at) > RETENTION_SECONDS
    ]
    for rid in dead:
        _RUNS.pop(rid, None)
    return len(dead)


def stats() -> dict:
    """诊断用：登记簿里还有多少条运行、其中多少在跑。"""
    return {
        "runs": len(_RUNS),
        "running": sum(1 for r in _RUNS.values() if not r.done),
        "tasks": len(_TASKS),
    }
