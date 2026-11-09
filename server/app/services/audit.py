"""审计日志：记录每一次对外开放 API 的调用。

与指标（metrics）的分工：指标关注"系统跑得怎么样"（延迟、token、错误率），
审计关注"谁在什么时候调了什么"——按工作区/密钥可追溯，用于计费与安全审查。

存储：一张 append-only 的 `audit_logs` 表，一条日志一行。
早先是「整域一份 JSON」，每来一个请求就要重写上百 KB，写放大极其夸张；
现在记一条只 INSERT 一行，超过条数上限再按最旧的一批 DELETE。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import AUDIT_LOGS
from app.services.state import JsonState

MAX_ENTRIES = 3000

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "audit.json"

_lock = threading.Lock()
# JSON 后端（无库降级路径）仍是「整份列表」语义
_json_state = JsonState(STORE_PATH)
_entries: list[dict] = []
_loaded = False
_table: TableState | None = None


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table_state() -> TableState:
    global _table
    if _table is None:
        db = get_db()
        migrate(db)
        _table = TableState(AUDIT_LOGS, db)
    return _table


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        if _use_table():
            # 只把最近 MAX_ENTRIES 条装进内存缓存（新→旧排序后翻转成旧→新）
            recent = _table_state().rows(limit=MAX_ENTRIES, order="ts DESC, id DESC")
            _entries.extend(reversed(recent))
        else:
            payload = _json_state.read()
            if isinstance(payload, list):
                _entries.extend(payload[-MAX_ENTRIES:])
    except Exception:  # noqa: BLE001 — 损坏则丢弃
        pass
    _loaded = True


def record(
    *,
    workspace_id: str,
    key_id: str | None,
    key_name: str | None,
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    detail: str = "",
) -> None:
    entry = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace_id": workspace_id,
        "key_id": key_id,
        "key_name": key_name,
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 1),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "detail": detail,
    }
    with _lock:
        _ensure_loaded()
        _entries.append(entry)
        overflow = len(_entries) - MAX_ENTRIES
        if overflow > 0:
            del _entries[:overflow]
        _persist(entry, overflow)


def _persist(entry: dict, overflow: int) -> None:
    if _use_table():
        table = _table_state()
        table.insert_many([entry])
        if overflow > 0:
            # 只保留最近 MAX_ENTRIES 条：按 ts 升序删掉最旧的一批
            table.delete_where(
                "id IN (SELECT id FROM audit_logs ORDER BY ts ASC, id ASC LIMIT ?)",
                (overflow,),
            )
        return
    _json_state.write(_entries[-MAX_ENTRIES:])


def list_entries(limit: int = 100, workspace_id: str | None = None) -> list[dict]:
    with _lock:
        _ensure_loaded()
        rows = list(reversed(_entries))
    if workspace_id:
        # 鉴权失败的调用没有归属工作区（记 "-"），也要在视图里露出，便于发现异常探测
        rows = [r for r in rows if r["workspace_id"] in (workspace_id, "-")]
    return rows[:limit]


def summary(workspace_id: str | None = None) -> dict:
    rows = list_entries(limit=MAX_ENTRIES, workspace_id=workspace_id)
    total = len(rows)
    errors = sum(1 for r in rows if r["status"] >= 400)
    return {
        "total": total,
        "errors": errors,
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "keys": len({r["key_id"] for r in rows if r["key_id"]}),
    }
