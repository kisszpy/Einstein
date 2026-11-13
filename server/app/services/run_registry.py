"""运行登记簿：一条任务的执行与「谁在看」彻底解耦。

**为什么需要**：FastAPI 的 `StreamingResponse` 在客户端断开时会取消生成器。
旧实现把执行写在生成器里，于是浏览器切走标签页、网络闪断、用户点「停止」，
都会连带把任务掐掉——产物可能已经写了一半，运行记录却查不到，用户回来
只看到「什么都没有」。这里把执行放进独立 task，事件写进登记簿；SSE 只是
登记簿的一个读者，断开不影响执行，重连还能从断点继续看。

代价是事件会在内存里留一段时间（默认 30 分钟），所以有 TTL 清理：任务
结束且超过保留期就整条丢掉。这跟「运行历史」是两件事——历史落库，
登记簿只服务于「这次运行还没看完」这一段。

**进程外可见**：内存登记簿只对「创建它的那个进程」有效。多 worker 部署时
用户刷新页面可能被负载均衡打到别的进程，那里自然找不到这次运行。所以
`push()` 会把有重放价值的事件同步写一份到 `run_events`（见 `run_events.py`），
`follow_persisted()` 让任何进程都能从库重放并跟到结束。内存是快路径，
库是兜底路径，两者是同一份日志的两层缓存。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

from app.core.logging import get_logger

logger = get_logger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

from app.services import run_events

# 结束后的保留时长：够用户切回来、刷新、或换个设备重连
RETENTION_SECONDS = 1800
# 单条运行最多留多少帧，防止长任务把内存吃穿（超出丢弃最早的步骤帧）
MAX_EVENTS = 4000

_RUNS: dict[str, RunRecord] = {}
_TASKS: dict[str, asyncio.Task] = {}
# 每登记多少次顺带清一次库里的过期事件（不必每次都清）
_prune_tick = 0


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
    # 墙上时间：`created_at` 是 monotonic，只适合算 TTL，不能拿去和库里的
    # ISO 时间比大小（两者相减没有意义）。对外展示「哪一轮更新」用这个。
    started_at: str = field(default_factory=_iso_now)
    # 事件序号：内存列表下标会被 MAX_EVENTS 截断，库里的 seq 必须单调
    seq: int = 0
    # 落库失败一次就关掉，避免每个事件都重试一遍 IO 超时
    persisted: bool = True
    # 谁按下了停止："" = 没停过；"user" = 用户主动停止；"shutdown" = 服务关闭。
    # 执行方据此决定「这轮怎么结束」——主动停止要落 stopped 状态并给一句人话，
    # 而不是笼统写成「服务正在关闭」。
    cancel_reason: str = ""
    # 消费者用它等待「有新事件了」；push 时 set，消费完 clear
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, event: str, data: dict) -> None:
        if len(self.events) >= MAX_EVENTS:
            # 极端长任务（上万次工具调用）才会触发；丢最早的帧保住最新进展
            self.events = self.events[-MAX_EVENTS:]
        self.events.append((event, data))
        self.seq += 1
        if self.persisted and event in run_events.DURABLE:
            ok = run_events.append(
                run_id=self.run_id, seq=self.seq, event=event, data=data,
                workspace_id=self.workspace_id, owner_id=self.owner_id,
                session_id=self.session_id,
            )
            if not ok:
                self.persisted = False
                logger.warning("%s 事件落库失败，退回纯内存", self.run_id)
        self.changed.set()

    def finish(self, *, failed: bool = False) -> None:
        self.done = True
        self.failed = failed
        self.changed.set()


def create(*, session_id: str, workspace_id: str, owner_id: str) -> RunRecord:
    """登记一次新运行。run_id 与 session_id 不同：一个会话可以有多轮运行。"""
    global _prune_tick
    purge()
    _prune_tick += 1
    if _prune_tick % 50 == 0:
        # 顺带清一次库里的过期事件：没有独立定时器，靠登记频率驱动就够
        run_events.prune()
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


def list_for_owner(
    workspace_id: str, owner_id: str, *, include_done: bool = False,
) -> list[RunRecord]:
    """某人当前空间里的运行。前端刷新后用它找回「还没看完的那一轮」。"""
    rows = [
        r for r in _RUNS.values()
        if r.workspace_id == workspace_id and r.owner_id == owner_id
    ]
    if not include_done:
        rows = [r for r in rows if not r.done]
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


def active(workspace_id: str, owner_id: str) -> list[dict]:
    """「还没看完的运行」，本进程 + 库里的并集。

    本进程的记录更新得最及时（内存直读），库里的能补上「在别的副本上创建、
    本进程从没见过」的那些。按 run_id 去重，内存优先。
    """
    rows: dict[str, dict] = {}
    for r in list_for_owner(workspace_id, owner_id):
        rows[r.run_id] = {
            "run_id": r.run_id,
            "session_id": r.session_id,
            "created_at": r.started_at,
            "done": r.done,
            "failed": r.failed,
            "steps": len(r.events),
            "local": True,
        }
    for item in run_events.running(workspace_id, owner_id):
        rid = item["run_id"]
        if rid in rows:
            continue
        rows[rid] = {**item, "steps": 0, "local": False}
    # 同一秒内创建的两次运行（本进程 + 别的副本）按 local 兜底：本进程的更新
    return sorted(
        rows.values(),
        key=lambda r: (r.get("created_at") or "", bool(r.get("local"))),
        reverse=True,
    )


def has_persisted(run_id: str, *, workspace_id: str, owner_id: str) -> bool:
    """库里有没有这次运行。内存里没有时才需要问（重连走跨进程路径）。"""
    return run_events.exists(run_id, workspace_id=workspace_id, owner_id=owner_id)


async def follow_persisted(
    run_id: str, *, workspace_id: str, owner_id: str,
    poll: float = 0.5, idle_seconds: float = 900.0,
) -> "AsyncIterator[tuple[str, dict]]":
    """从库重放一次运行，然后轮询新增事件，直到看到终态。

    轮询而不是订阅，是因为事件可能写在任何一个进程里，没有跨进程的通知
    通道；0.5 秒一次、每次只按 `seq > 游标` 取增量，代价可忽略。`idle_seconds`
    是最后一道保险：写「终态事件」的那一瞬进程被杀，库里会永远停在中间，
    这时宁可断开也不要让连接一直挂着。
    """
    cursor = 0
    quiet = 0.0
    while True:
        fresh = run_events.replay(
            run_id, workspace_id=workspace_id, owner_id=owner_id, after_seq=cursor,
        )
        if not fresh:
            if quiet >= idle_seconds:
                return
            await asyncio.sleep(poll)
            quiet += poll
            continue
        quiet = 0.0
        for seq, event, data in fresh:
            cursor = seq
            yield event, data
            if event in run_events.TERMINAL:
                return


def attach_task(run_id: str, task: asyncio.Task) -> None:
    """持有后台任务的强引用：事件循环只持弱引用，否则任务可能被 GC 掉。"""
    _TASKS[run_id] = task
    task.add_done_callback(lambda _t: _TASKS.pop(run_id, None))


def cancel(run_id: str, *, workspace_id: str, owner_id: str) -> str:
    """停下一次正在跑的运行。

    返回 ``"cancelled"``（已下发取消）/ ``"done"``（已经结束）/ ``"abandoned"``
    （库里只剩一个没收尾的记录，本进程没有对应任务）/ ``"remote"``
    （任务在别的副本上，且还在持续产出事件）/ ``"missing"``（查无此运行）。

    为什么要有 `remote` 这一档：登记簿的内存部分是进程内的，多副本部署时
    用户点停止可能落在没有这个 task 的进程上。这时**不能假装停掉了**——
    任务还在烧 token。调用方据此回一句「任务在另一个实例上运行，本实例停不了」，
    而不是回 200 让用户以为停了。

    为什么还要有 `abandoned`：进程重启后内存寄存器清空，库里那条没收尾的
    记录却还在（上一个进程被杀，`done` 没落库）。用户点停止，旧实现直接
    404，界面上就出现「一个永远在跑、又停不掉的幽灵任务」——它同时锁死了
    任务切换。这种情况补一条终态事件当场收尾，让停止真的生效。
    """
    record = get(run_id, workspace_id=workspace_id, owner_id=owner_id)
    if record is None:
        # 内存里没有：要么是别人的/过期的，要么是上一个进程的尸体。
        # 后者要能停——按库里最近一次事件判断它是不是还在动。
        items = [
            # stale_seconds=0：用户既然点了停止，再老也要能停掉，
            # 不能因为「太久没动」就回一句查无此运行。
            item for item in run_events.running(workspace_id, owner_id, stale_seconds=0)
            if item.get("run_id") == run_id
        ]
        if not items:
            return "missing"
        # 库里还在持续产出事件：真的跑在别的副本上，不能假称停掉
        if not run_events.abandon(
            run_id=run_id, workspace_id=workspace_id, owner_id=owner_id,
            session_id=str(items[0].get("session_id") or ""),
            reason="已按你的要求停止：这次运行在当前实例上已不可继续。",
        ):
            return "remote"
        logger.info("%s 已收尾（库里遗留的未完成运行）", run_id)
        return "abandoned"
    if record.done:
        return "done"
    task = _TASKS.get(run_id)
    if task is None:
        return "remote"
    record.cancel_reason = "user"
    task.cancel()
    logger.info("%s 已按用户请求停止", run_id)
    return "cancelled"


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
        # 落库失败退回纯内存的运行数：>0 说明这个进程的运行跨不了副本
        "memory_only": sum(1 for r in _RUNS.values() if not r.persisted),
    }
