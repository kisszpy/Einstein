"""审计日志：记录每一次对外开放 API 的调用。

与指标（metrics）的分工：指标关注"系统跑得怎么样"（延迟、token、错误率），
审计关注"谁在什么时候调了什么"——按工作区/密钥可追溯，用于计费与安全审查。
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "audit.json"
_STATE = blob("audit", STORE_PATH)

MAX_ENTRIES = 3000

_entries: list[dict] = []
_lock = threading.Lock()
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        payload = _STATE.read()
        if isinstance(payload, list):
            _entries.extend(payload[-MAX_ENTRIES:])
    except Exception:  # noqa: BLE001 — 损坏则丢弃
        pass
    _loaded = True


def _flush() -> None:
    _STATE.write(_entries[-MAX_ENTRIES:])


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
        if len(_entries) > MAX_ENTRIES:
            del _entries[: len(_entries) - MAX_ENTRIES]
        _flush()


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
