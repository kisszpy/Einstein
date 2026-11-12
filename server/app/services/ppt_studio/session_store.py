"""PPT 会话 / 消息 / 生成记录的存储层，照 services/templates.py 双后端范式。

三份数据各一个文件/表：会话（含状态机与页数口径）、消息（payload 里放
确认卡与结果卡）、生成记录（历次结果卡，重新签票靠它）。运行类数据按
owner 隔离：不是本人的会话一律当不存在（404，不泄露存在性）。
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
from app.db.tables import PPT_GENERATIONS, PPT_MESSAGES, PPT_SESSIONS
from app.services.state import JsonState

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_SESSIONS_JSON = JsonState(_DATA_DIR / "ppt_sessions.json")
_MESSAGES_JSON = JsonState(_DATA_DIR / "ppt_messages.json")
_GENERATIONS_JSON = JsonState(_DATA_DIR / "ppt_generations.json")
_lock = threading.Lock()

STATUSES = ("idle", "analyzing", "awaiting_confirm", "generating", "done", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _tables() -> dict[str, TableState]:
    db = get_db()
    migrate(db)
    return {
        "sessions": TableState(PPT_SESSIONS, db),
        "messages": TableState(PPT_MESSAGES, db),
        "generations": TableState(PPT_GENERATIONS, db),
    }


def _rows(kind: str) -> list[dict]:
    if _use_table():
        tables = _tables()
        table = {"sessions": PPT_SESSIONS, "messages": PPT_MESSAGES, "generations": PPT_GENERATIONS}[kind]
        return tables[kind].rows()
    data = {"sessions": _SESSIONS_JSON, "messages": _MESSAGES_JSON, "generations": _GENERATIONS_JSON}[kind]
    return [r for r in (data.read() or []) if isinstance(r, dict)]


def _save_rows(kind: str, rows: list[dict]) -> None:
    if _use_table():
        tables = _tables()
        table = {"sessions": PPT_SESSIONS, "messages": PPT_MESSAGES, "generations": PPT_GENERATIONS}[kind]
        tables[kind].write({r["id"]: r for r in rows})
        return
    data = {"sessions": _SESSIONS_JSON, "messages": _MESSAGES_JSON, "generations": _GENERATIONS_JSON}[kind]
    data.write(rows)


# ---------------------------------------------------------------- 会话


def visible(session: dict, workspace_id: str, owner_id: str) -> bool:
    return session.get("workspace_id") == workspace_id and session.get("owner_id") == owner_id


def create_session(
    *,
    workspace_id: str,
    owner_id: str,
    title: str = "",
    project: str = "",
    expert_id: str = "",
    skill_ids: list[str] | None = None,
    kb_ids: list[str] | None = None,
    theme_pack_id: str = "",
) -> dict:
    entry = {
        "id": f"ps_{uuid.uuid4().hex[:12]}",
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "title": (title or "新演示").strip()[:60],
        "project": project,
        "expert_id": expert_id,
        "skill_ids": list(skill_ids or []),
        "kb_ids": list(kb_ids or []),
        "theme_pack_id": theme_pack_id,
        "status": "idle",
        "clarify_count": 0,
        "page_count": 0,
        "total_slides": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        rows = _rows("sessions")
        rows.append(entry)
        _save_rows("sessions", rows)
    return entry


def list_sessions(workspace_id: str, owner_id: str, limit: int = 50) -> list[dict]:
    rows = [s for s in _rows("sessions") if visible(s, workspace_id, owner_id)]
    rows.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return rows[: max(limit, 1)]


def get_session(session_id: str) -> dict | None:
    return next((s for s in _rows("sessions") if s.get("id") == session_id), None)


def require_session(session_id: str, workspace_id: str, owner_id: str) -> dict | None:
    """不可见与不存在同一个返回（None，路由层转 404）。"""
    session = get_session(session_id)
    if session is None or not visible(session, workspace_id, owner_id):
        return None
    return session


def update_session(session_id: str, patch: dict) -> dict | None:
    with _lock:
        rows = _rows("sessions")
        entry = next((s for s in rows if s.get("id") == session_id), None)
        if entry is None:
            return None
        allowed = {
            "title", "project", "expert_id", "skill_ids", "kb_ids",
            "theme_pack_id", "status", "clarify_count", "page_count", "total_slides",
            # 每份稿子由内容决定的设计方向；预览与生成要读到同一份，不能各算各的
            "design_direction",
        }
        for key, value in patch.items():
            if key in allowed and value is not None:
                entry[key] = value
        entry["updated_at"] = _now()
        _save_rows("sessions", rows)
    return entry


def update_status(session_id: str, status: str, **fields: object) -> dict | None:
    return update_session(session_id, {"status": status, **fields})


def bump_clarify(session_id: str) -> int:
    """歧义追问计数 +1，返回新值。>=1 后不再允许追问。"""
    with _lock:
        rows = _rows("sessions")
        entry = next((s for s in rows if s.get("id") == session_id), None)
        if entry is None:
            return 0
        entry["clarify_count"] = int(entry.get("clarify_count") or 0) + 1
        entry["updated_at"] = _now()
        _save_rows("sessions", rows)
        return int(entry["clarify_count"])


def delete_session(session_id: str) -> tuple[bool, int]:
    """删会话与其消息/生成记录；已落盘的 deck 文件不动（用户成果不丢）。"""
    with _lock:
        sessions = _rows("sessions")
        kept = [s for s in sessions if s.get("id") != session_id]
        if len(kept) == len(sessions):
            return False, 0
        _save_rows("sessions", kept)
        messages = _rows("messages")
        removed = len([m for m in messages if m.get("session_id") == session_id])
        _save_rows("messages", [m for m in messages if m.get("session_id") != session_id])
        generations = _rows("generations")
        _save_rows("generations", [g for g in generations if g.get("session_id") != session_id])
    return True, removed


def session_summary(session: dict) -> dict:
    return {
        "id": session.get("id", ""),
        "title": session.get("title", ""),
        "project": session.get("project", ""),
        "expert_id": session.get("expert_id", ""),
        "skill_ids": session.get("skill_ids") or [],
        "kb_ids": session.get("kb_ids") or [],
        "theme_pack_id": session.get("theme_pack_id", ""),
        "status": session.get("status", "idle"),
        "clarify_count": int(session.get("clarify_count") or 0),
        "page_count": int(session.get("page_count") or 0),
        "total_slides": int(session.get("total_slides") or 0),
        "created_at": session.get("created_at", ""),
        "updated_at": session.get("updated_at", ""),
    }


# ---------------------------------------------------------------- 消息


def append_message(
    session_id: str, role: str, content: str, payload: dict | None = None,
    message_id: str | None = None,
) -> dict:
    """追加一条消息；seq 由这里管（max+1），payload 放确认卡/结果卡。"""
    with _lock:
        messages = _rows("messages")
        seq = max((int(m.get("seq") or 0) for m in messages if m.get("session_id") == session_id), default=0) + 1
        entry = {
            "id": message_id or f"pm_{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "seq": seq,
            "role": role,
            "content": content,
            "payload": payload or {},
            "created_at": _now(),
        }
        messages.append(entry)
        _save_rows("messages", messages)
    return entry


def list_messages(session_id: str) -> list[dict]:
    rows = [m for m in _rows("messages") if m.get("session_id") == session_id]
    rows.sort(key=lambda m: int(m.get("seq") or 0))
    return rows


def message_payload(session_id: str, message_id: str) -> dict | None:
    entry = next(
        (m for m in _rows("messages") if m.get("session_id") == session_id and m.get("id") == message_id),
        None,
    )
    return entry.get("payload") if entry else None


def update_message_payload(session_id: str, message_id: str, patch: dict) -> dict | None:
    """覆盖消息 payload（保存确认卡时把用户编辑后的 outline 写回去）。"""
    with _lock:
        messages = _rows("messages")
        entry = next(
            (m for m in messages if m.get("session_id") == session_id and m.get("id") == message_id),
            None,
        )
        if entry is None:
            return None
        payload = entry.get("payload") or {}
        payload.update(patch)
        entry["payload"] = payload
        _save_rows("messages", messages)
    return entry


def latest_outline(session_id: str) -> tuple[str, dict] | None:
    """最近一份已保存的大纲 → (message_id, outline)。generate 省略 outline 时用。"""
    for message in reversed(list_messages(session_id)):
        payload = message.get("payload") or {}
        outline = payload.get("outline")
        if isinstance(outline, dict) and outline.get("sections"):
            return message["id"], outline
    return None


# ---------------------------------------------------------------- 生成记录


def insert_generation(
    *,
    session_id: str,
    project: str,
    deck_path: str,
    total_slides: int,
    layouts_used: list[str],
    fidelity: dict,
    status: str = "ok",
    error: str = "",
) -> dict:
    entry = {
        "id": f"gen_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "project": project,
        "deck_path": deck_path,
        "total_slides": total_slides,
        "layouts_used": list(layouts_used),
        "fidelity": fidelity,
        "status": status,
        "error": error,
        "created_at": _now(),
    }
    with _lock:
        rows = _rows("generations")
        rows.append(entry)
        _save_rows("generations", rows)
    return entry


def list_generations(session_id: str) -> list[dict]:
    rows = [g for g in _rows("generations") if g.get("session_id") == session_id]
    rows.sort(key=lambda g: g.get("created_at", ""), reverse=True)
    return rows


def get_generation(generation_id: str) -> dict | None:
    """按 id 取一条生成记录（缩略图路由要先拿它判归属）。"""
    return next((g for g in _rows("generations") if g.get("id") == generation_id), None)


def generation_payload(generation: dict, deck: dict | None = None) -> dict:
    from app.services.ppt_studio import thumbs

    generation_id = generation.get("id", "")
    payload = {
        "generation_id": generation_id,
        "project": generation.get("project", ""),
        "deck_path": generation.get("deck_path", ""),
        "total_slides": int(generation.get("total_slides") or 0),
        "layouts_used": generation.get("layouts_used") or [],
        "fidelity": generation.get("fidelity") or {},
        "status": generation.get("status", "ok"),
        "error": generation.get("error", ""),
        "created_at": generation.get("created_at", ""),
        "thumb_count": thumbs.count(generation_id),
    }
    if deck:
        payload["deck"] = deck
    return payload
