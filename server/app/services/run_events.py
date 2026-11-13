"""运行事件落库：让「谁在看」和「在哪台机器上看」也解耦。

`run_registry` 原本是纯进程内字典，单进程下工作得很好。一旦多 worker 或
多副本，用户在跑到一半时刷新页面，负载均衡把他打到另一个进程，那个进程
的登记簿里当然没有这次运行——任务其实还在跑，只是没人看得见。前端表现
是「转圈转一半没了」，比直接报错更难查。

这里把**有重放价值的事件**写进 `run_events` 表，任何一个进程都能按 seq
顺序读回来。内存里的登记簿继续负责低延迟（本进程拿事件不用等 IO），
库只负责「跨进程可见」，两者是同一份日志的两层缓存，不是两套真相。

为什么不落 `delta`：一次回答可能有几千帧，每帧一行会把「流式输出」变成
「写数据库」。重连时正文本来就会从会话历史里回来，少一段逐字动画不算
信息丢失。为什么不落 `ping`：那是心跳，不是事件。

两种后端形状一致：
- `sqlite` / `postgres` → `run_events` 表，一行一个事件
- `json`（本地无库的降级路径）→ `data/run_events/<run_id>.jsonl`，一行一个事件

json 后端刻意用 JSONL 而不是整域一份 JSON：追加写只 append 一行，
不必每次把整次运行重写一遍。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import RUN_EVENTS

logger = get_logger(__name__)

# 落库的事件名：有「重放价值」的离散瞬间。
# 漏掉某个名字的后果是「重连后少一段」，不会报错——所以新增事件时记得回来加。
DURABLE = frozenset({
    "step",          # 工具调用 / 思考步骤
    "artifact",      # 产物（已写盘，重放不重做，只是把卡片补回界面）
    "answer_start",  # 开始作答（决定前端是否清空占位）
    "answer_reset",  # 丢弃已流出的一半回答（重试 / 改写）
    "citations",     # 引用列表
    "done",          # 正常结束
    "error",         # 异常结束
})

# 读到这些事件就说明这次运行不会再有后续了
TERMINAL = frozenset({"done", "error"})

# 单次运行最多留多少条（与 run_registry.MAX_EVENTS 同量级）。
# 超出后从最旧的开始丢：长任务的「最近进展」比「开头」更值得留。
MAX_PER_RUN = 2000

# 多久没有新事件就不再算「还在跑」。
#
# 这个值存在的唯一原因：进程被杀时 `done` 来不及落库，那条运行在库里会
# 永远停在中间。`active()` 把它当活跃运行返回，前端刷新后被挂上一条永不
# 结束的流，接着 `running` 锁住整个页面——任务切不动、新任务发不出，用户
# 只能去点「停止」。10 分钟是「真的还在思考」和「其实是尸体」之间的折中：
# 真跑着的长任务至少会周期性推 tool_result，不会连续十分钟一片空白。
STALE_SECONDS = 600

_DIR = Path(__file__).resolve().parents[2] / "data" / "run_events"

_lock = threading.Lock()
_table: TableState | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def use_table() -> bool:
    """是否走数据库。json 后端下写 JSONL，语义相同只是介质不同。"""
    return get_settings().store_backend != "json"


def _state() -> TableState:
    global _table
    if _table is None:
        db = get_db()
        migrate(db)
        _table = TableState(RUN_EVENTS, db)
    return _table


def _path(run_id: str) -> Path:
    # run_id 是 uuid4 的 hex，只含 0-9a-f，直接当文件名安全；
    # 仍做一次白名单过滤，避免调用方传进奇怪的值（这是写文件的接口）
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")[:64]
    return _DIR / f"{safe or 'unknown'}.jsonl"


def _row(*, run_id: str, seq: int, event: str, data: dict, **meta: str) -> dict:
    return {
        "id": f"{run_id}:{seq:06d}",
        "run_id": run_id,
        "seq": int(seq),
        "event": event,
        "data": data,
        "workspace_id": meta.get("workspace_id", ""),
        "owner_id": meta.get("owner_id", ""),
        "session_id": meta.get("session_id", ""),
        "created_at": _now(),
    }


# ---------------------------------------------------------------- 写


def append(
    *, run_id: str, seq: int, event: str, data: dict,
    workspace_id: str = "", owner_id: str = "", session_id: str = "",
) -> bool:
    """写一个事件，返回是否真的写成功。

    失败一律吞掉并返回 False：运行本身不能被「记日志失败」打断。调用方
    收到 False 会关掉持久化，免得每个事件都重试一次 IO 超时。
    """
    if event not in DURABLE:
        return False
    row = _row(
        run_id=run_id, seq=seq, event=event, data=data,
        workspace_id=workspace_id, owner_id=owner_id, session_id=session_id,
    )
    try:
        with _lock:
            if use_table():
                _state().insert_many([row])
            else:
                path = _path(run_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001 — 记日志失败不该影响正在跑的任务
        logger.warning("事件写入失败 %s#%s %s: %s: %s", run_id, seq, event, type(exc).__name__, exc)
        return False
    return True


# ---------------------------------------------------------------- 读


def _jsonl_rows(run_id: str) -> list[dict]:
    path = _path(run_id)
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    # 进程在写这一行时被杀会留下半行；跳过而不是整份作废
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


def replay(
    run_id: str, *, workspace_id: str, owner_id: str,
    after_seq: int = 0, limit: int = 4000,
) -> list[tuple[int, str, dict]]:
    """按 seq 升序读回事件。归属不符返回空（不泄露存在性）。

    `after_seq` 用于跨进程跟读：只取比分片游标更新的事件。
    """
    try:
        if use_table():
            rows = _state().rows(
                "run_id = ? AND workspace_id = ? AND owner_id = ? AND seq > ?",
                (run_id, workspace_id, owner_id, int(after_seq)),
                limit=limit, order="seq ASC",
            )
        else:
            rows = [
                r for r in _jsonl_rows(run_id)
                if r.get("workspace_id") == workspace_id
                and r.get("owner_id") == owner_id
                and int(r.get("seq") or 0) > int(after_seq)
            ]
            rows.sort(key=lambda r: int(r.get("seq") or 0))
            rows = rows[:limit]
    except Exception as exc:  # noqa: BLE001 — 表还没建好等，当作没有
        logger.warning("事件读取失败 %s: %s: %s", run_id, type(exc).__name__, exc)
        return []

    out: list[tuple[int, str, dict]] = []
    for row in rows:
        data = row.get("data")
        out.append((
            int(row.get("seq") or 0),
            str(row.get("event") or ""),
            data if isinstance(data, dict) else {},
        ))
    return out


def exists(run_id: str, *, workspace_id: str, owner_id: str) -> bool:
    """库里有没有这次运行（判断重连该 404 还是该重放）。"""
    return bool(replay(run_id, workspace_id=workspace_id, owner_id=owner_id, limit=1))


def _group(rows: list[dict]) -> list[dict]:
    """把事件行折叠成「一次运行一条」的摘要，只留未结束的。

    rows 按时间倒序传入，同一个 run 第一次出现的就是它最新的事件——
    据此判断这次运行是不是已经收尾。
    """
    seen: dict[str, dict] = {}
    for row in rows:
        rid = str(row.get("run_id") or "")
        if not rid or rid in seen:
            continue
        event = str(row.get("event") or "")
        seen[rid] = {
            "run_id": rid,
            "session_id": str(row.get("session_id") or ""),
            "created_at": str(row.get("created_at") or ""),
            "done": event in TERMINAL,
            "failed": event == "error",
        }
    return [item for item in seen.values() if not item["done"]]


def running(
    workspace_id: str, owner_id: str, *, limit: int = 3000,
    stale_seconds: int = STALE_SECONDS,
) -> list[dict]:
    """当前空间里「库里有、但还没写终态事件」的运行。

    一次最多捞最近 limit 条事件再折叠：正在跑的那几次一定在最前面，
    被截断的只可能是早就结束、只剩清理没做干净的老运行。

    `created_at` 是该运行**最新一条事件**的时间，所以「太久没动」等价于
    「它其实已经死了」。不滤掉的话，一次进程重启会给用户留下一个永远
    「正在运行」的任务，前端每次刷新都挂上去、锁死十五分钟。
    """
    try:
        if use_table():
            rows = _state().rows(
                "workspace_id = ? AND owner_id = ?",
                (workspace_id, owner_id),
                limit=limit, order="created_at DESC, id DESC",
            )
        else:
            rows = []
            if _DIR.exists():
                for path in sorted(_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
                    rows.extend(reversed(_jsonl_rows(path.stem)))
                    if len(rows) >= limit:
                        break
            rows = rows[:limit]
    except Exception:  # noqa: BLE001
        return []
    items = _group(rows)
    if stale_seconds <= 0:
        return items
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=int(stale_seconds))
    ).isoformat(timespec="seconds")
    return [item for item in items if str(item.get("created_at") or "") >= cutoff]


def _latest_seq(run_id: str) -> int:
    """该运行已落库的最大 seq；补终态事件时要接在它后面。"""
    try:
        if use_table():
            rows = _state().rows("run_id = ?", (run_id,), limit=1, order="seq DESC")
        else:
            rows = sorted(
                _jsonl_rows(run_id), key=lambda r: int(r.get("seq") or 0), reverse=True,
            )[:1]
    except Exception:  # noqa: BLE001
        return 0
    if not rows:
        return 0
    try:
        return int(rows[0].get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def abandon(
    *, run_id: str, workspace_id: str, owner_id: str, session_id: str = "",
    reason: str = "服务重启，本次运行已中断。",
) -> bool:
    """给一次「有始无终」的运行补一条终态事件。

    进程被杀、容器被重启时，`done` 没机会落库。库里这条运行就永远停在
    中间：`active()` 反复把它当「还在跑」，前端挂上一条永不结束的流。
    补一条 `error` 把它收尾，此后任何进程都不会再认它作活跃运行。
    """
    return append(
        run_id=run_id, seq=_latest_seq(run_id) + 1, event="error",
        data={"message": reason, "abandoned": True},
        workspace_id=workspace_id, owner_id=owner_id, session_id=session_id,
    )


def orphans(*, limit: int = 4000) -> list[dict]:
    """全库范围内「没有终态事件」的运行，带上归属信息供补收尾用。"""
    try:
        if use_table():
            rows = _state().rows("", (), limit=limit, order="created_at DESC, id DESC")
        else:
            rows = []
            if _DIR.exists():
                for path in sorted(_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
                    rows.extend(reversed(_jsonl_rows(path.stem)))
                    if len(rows) >= limit:
                        break
            rows = rows[:limit]
    except Exception:  # noqa: BLE001
        return []

    seen: dict[str, dict] = {}
    for row in rows:
        rid = str(row.get("run_id") or "")
        if not rid or rid in seen:
            continue
        seen[rid] = {
            "run_id": rid,
            "workspace_id": str(row.get("workspace_id") or ""),
            "owner_id": str(row.get("owner_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "created_at": str(row.get("created_at") or ""),
            "last_event": str(row.get("event") or ""),
        }
    return [item for item in seen.values() if item["last_event"] not in TERMINAL]


def abandon_orphans(*, before: str = "", reason: str = "服务重启，本次运行已中断。") -> int:
    """把启动前遗留的未收尾运行全部补上终态，返回处理条数。

    调用方只在「本进程刚起来、内存登记簿一定是空的」这一刻调用：此时库里
    所有未收尾的运行都不可能是本进程在跑，只能是上一个进程的尸体。

    `before` 是 ISO 时间（本进程启动时刻）。多副本滚动重启时，别的副本
    正在跑的任务也满足「未收尾」，因此只在单副本部署下信任这一步；多副本
    请关掉 `agent_run_orphan_sweep`，靠 `running()` 的陈旧阈值兜底。
    """
    fixed = 0
    for item in orphans():
        created = str(item.get("created_at") or "")
        if before and created >= before:
            # 启动之后才产生的事件：可能真的是别的副本在跑
            continue
        ok = abandon(
            run_id=item["run_id"],
            workspace_id=item["workspace_id"],
            owner_id=item["owner_id"],
            session_id=item["session_id"],
            reason=reason,
        )
        if ok:
            fixed += 1
    return fixed


# ---------------------------------------------------------------- 清理


def prune(hours: int = 24) -> int:
    """删掉过期运行事件，返回删掉的条数。

    TTL 与登记簿的 30 分钟保留期是两个概念：登记簿只服务「这次运行还没看完」，
    这里要留够「用户明天回来还想接上」的时间，默认一天。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(int(hours), 1))).isoformat()
    removed = 0
    try:
        if use_table():
            removed = _state().delete_where("created_at < ?", (cutoff,))
        else:
            if _DIR.exists():
                for path in _DIR.glob("*.jsonl"):
                    try:
                        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() < cutoff:
                            path.unlink()
                            removed += 1
                    except OSError:
                        continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("事件清理失败: %s: %s", type(exc).__name__, exc)
    return removed
