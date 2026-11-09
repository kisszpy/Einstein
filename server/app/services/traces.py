"""按 run 的运行轨迹：一次问答 / 一次智能体任务一行。

为什么不是全链路 tracing：引 OTel 要先起 collector、改所有调用点、配采样，
对一个自托管产品是过度工程。这里只回答一个具体问题——
「这次答错，是检索没召回、召回错、工具选错，还是模型跑偏」。

写入策略是「一次落库」：`start()` 在内存里攒上下文，检索与工具调用
用 `note_*` 记到同一份缓冲，`finish()` 时整行写入。中途进程被杀会丢
这一条 trace，但不会让每个工具调用都写一次库（那会拖慢工具本身）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.services.state import JsonState

_DATA = Path(__file__).resolve().parents[2] / "data"
_JSON = JsonState(_DATA / "run_traces.json")
MAX_ROWS = 5000

# 当前 run 的 id：agent 的图执行与检索在同一协程上下文里，ContextVar 能带上
_current: ContextVar[str] = ContextVar("current_trace", default="")
_pending: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table() -> TableState:
    db = get_db()
    migrate(db)
    from app.db.tables import TABLES

    return TableState(TABLES["run_traces"], db)


# ---------------------------------------------------------------- 采集


def start(
    *, kind: str, workspace_id: str, owner_id: str,
    session_id: str = "", engine: str = "", model: str = "",
) -> str:
    """开始一次运行，返回 run_id。同一协程内后续 note_* 都归到这条。"""
    run_id = uuid.uuid4().hex[:16]
    with _lock:
        _pending[run_id] = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "owner_id": owner_id,
            "kind": kind,
            "session_id": session_id,
            "engine": engine,
            "model": model,
            "started_at": _now(),
            "finished_at": "",
            "duration_ms": 0,
            "status": "ok",
            "error_kind": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "llm_calls": 0,
            "retrieval": {},
            "tool_calls": [],
            "interrupt": "",
            "_t0": time.perf_counter(),
        }
    _current.set(run_id)
    return run_id


def bind(run_id: str) -> Token | None:
    """把已存在的 run 绑定到当前上下文（重连/续跑时用）。"""
    if run_id in _pending:
        return _current.set(run_id)
    return None


def unbind(token: Token | None) -> None:
    if token is not None:
        try:
            _current.reset(token)
        except ValueError:
            _current.set("")


def current_run() -> str:
    return _current.get()


def note_retrieval(
    *, query: str = "", rewritten: str = "", mode: str = "",
    candidates: int = 0, hits: list | None = None, ms: int = 0,
    missed: bool = False, rerank: str = "",
) -> None:
    """检索一侧的事实：改写了什么、召回了多少、命中哪些、是否判为未命中。"""
    run_id = _current.get()
    if not run_id:
        return
    with _lock:
        item = _pending.get(run_id)
        if item is None:
            return
        item["retrieval"] = {
            "query": query[:500],
            "rewritten": rewritten[:500],
            "mode": mode,
            "candidates": candidates,
            "hits": (hits or [])[:10],
            "ms": ms,
            "missed": missed,
            "rerank": rerank,
        }


def note_tool(name: str, ms: int, status: str = "ok", detail: str = "") -> None:
    run_id = _current.get()
    if not run_id:
        return
    with _lock:
        item = _pending.get(run_id)
        if item is None:
            return
        item["tool_calls"].append(
            {"name": name, "ms": int(ms), "status": status, "detail": detail[:300]}
        )


def note_tokens(tokens_in: int = 0, tokens_out: int = 0, calls: int = 1) -> None:
    run_id = _current.get()
    if not run_id:
        return
    with _lock:
        item = _pending.get(run_id)
        if item is None:
            return
        item["tokens_in"] += max(int(tokens_in), 0)
        item["tokens_out"] += max(int(tokens_out), 0)
        item["llm_calls"] += max(int(calls), 0)


def finish(
    *, status: str = "ok", error_kind: str = "",
    interrupt: str = "", run_id: str | None = None,
) -> dict | None:
    """收尾并落库。重复调用是幂等的（第二次拿不到 pending 就返回 None）。"""
    target = run_id or _current.get()
    if not target:
        return None
    with _lock:
        item = _pending.pop(target, None)
    if item is None:
        return None
    t0 = item.pop("_t0", None)
    item["finished_at"] = _now()
    item["duration_ms"] = int((time.perf_counter() - t0) * 1000) if t0 else 0
    item["status"] = status
    item["error_kind"] = error_kind
    item["interrupt"] = interrupt
    _write(item)
    return item


def _write(row: dict) -> None:
    if _use_table():
        _table().upsert(row)
    else:
        rows = _JSON.read() or []
        rows.append(row)
        _JSON.write(rows[-MAX_ROWS:])


# ---------------------------------------------------------------- 查询


def list_runs(
    *, workspace_id: str | None = None, kind: str | None = None,
    status: str | None = None, limit: int = 50,
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    if _use_table():
        where, params = [], []
        if workspace_id:
            where.append("workspace_id = ?")
            params.append(workspace_id)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " AND ".join(where)
        rows = _table().rows(clause, tuple(params), limit=limit * 4)
        return sorted(rows, key=lambda r: r.get("started_at", ""), reverse=True)[:limit]

    rows = _JSON.read() or []
    if workspace_id:
        rows = [r for r in rows if r.get("workspace_id") == workspace_id]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: r.get("started_at", ""), reverse=True)[:limit]


def get_run(run_id: str) -> dict | None:
    if _use_table():
        rows = _table().rows("id = ?", (run_id,), limit=1)
        return rows[0] if rows else None
    return next((r for r in (_JSON.read() or []) if r.get("run_id") == run_id), None)


def summary(*, workspace_id: str | None = None, limit: int = 1000) -> dict:
    """近 N 条 run 的汇总：命中率、错误分布、平均耗时、token 合计。"""
    rows = list_runs(workspace_id=workspace_id, limit=limit)
    if not rows:
        return {
            "runs": 0, "error_rate": 0.0, "miss_rate": 0.0,
            "avg_ms": 0, "tokens_in": 0, "tokens_out": 0, "by_kind": {}, "errors": {},
        }
    errors: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    missed = 0
    retrieval_seen = 0
    for r in rows:
        by_kind[r.get("kind", "-")] = by_kind.get(r.get("kind", "-"), 0) + 1
        if r.get("status") != "ok":
            key = r.get("error_kind") or r.get("status") or "error"
            errors[key] = errors.get(key, 0) + 1
        ret = r.get("retrieval") or {}
        if isinstance(ret, str):
            try:
                ret = json.loads(ret)
            except Exception:  # noqa: BLE001
                ret = {}
        if ret:
            retrieval_seen += 1
            if ret.get("missed"):
                missed += 1
    return {
        "runs": len(rows),
        "error_rate": round(sum(errors.values()) / len(rows), 3),
        "miss_rate": round(missed / retrieval_seen, 3) if retrieval_seen else 0.0,
        "avg_ms": int(sum(int(r.get("duration_ms") or 0) for r in rows) / len(rows)),
        "tokens_in": sum(int(r.get("tokens_in") or 0) for r in rows),
        "tokens_out": sum(int(r.get("tokens_out") or 0) for r in rows),
        "by_kind": by_kind,
        "errors": errors,
    }
