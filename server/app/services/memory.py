"""消息反馈（赞/踩）与长期记忆。

反馈：按会话存 {rating, reason}，清单给管理员看，可一键转成评测用例。
记忆：工作区级事实库（手工添加 + 对话结束自动提炼，自动提炼默认关），
检索按关键词重合度取 top——事实条数少，不用向量。

两张表、两件事：
- `message_feedback` 一条反馈一行（会话、评分、原因）
- `workspace_memory`  一条事实一行（工作区、内容、来源）
原先两者都塞在「按工作区分组的整域 JSON」里，加一条要重写整组。
"""

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import MESSAGE_FEEDBACK, WORKSPACE_MEMORY
from app.services.state import JsonState

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON_FEEDBACK = JsonState(_DATA_DIR / "message_feedback.json")
_JSON_MEMORY = JsonState(_DATA_DIR / "workspace_memory.json")
_lock = threading.Lock()

MAX_FEEDBACK = 5000
MAX_MEMORY = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table(name: str) -> TableState:
    db = get_db()
    migrate(db)
    from app.db.tables import TABLES

    return TableState(TABLES[name], db)


# ---------------------------------------------------------------- 反馈

def add_feedback(
    *, workspace_id: str, session_id: str, conversation_id: str,
    rating: str, reason: str = "", turn: int = 1, user: str = "",
) -> dict:
    if rating not in ("up", "down"):
        raise ValueError("rating 只能是 up / down")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "workspace_id": workspace_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "turn": turn,
        "rating": rating,
        "reason": reason[:500],
        "user": user,
        "created_at": _now(),
    }
    with _lock:
        if _use_table():
            _table("message_feedback").upsert(entry)
        else:
            data = _JSON_FEEDBACK.read() or []
            data.append(entry)
            _JSON_FEEDBACK.write(data[-MAX_FEEDBACK:])
    return entry


def list_feedback(workspace_id: str | None = None, rating: str | None = None,
                  limit: int = 200) -> list[dict]:
    if _use_table():
        where: list[str] = []
        params: list = []
        if workspace_id is not None:
            where.append("workspace_id = ?")
            params.append(workspace_id)
        if rating is not None:
            where.append("rating = ?")
            params.append(rating)
        clause = " AND ".join(where)
        rows = _table("message_feedback").rows(
            clause, tuple(params), limit=limit, order="created_at DESC"
        )
        return rows
    data = _JSON_FEEDBACK.read() or []
    out = [e for e in data
           if (workspace_id is None or e.get("workspace_id") == workspace_id)
           and (rating is None or e.get("rating") == rating)]
    return out[-limit:][::-1]


def feedback_summary(workspace_id: str | None = None) -> dict:
    if _use_table():
        state = _table("message_feedback")
        clause, params = ("workspace_id = ?", (workspace_id,)) if workspace_id else ("", ())
        rows = state.rows(clause, params)
        up = sum(1 for e in rows if e["rating"] == "up")
        return {"up": up, "down": len(rows) - up, "total": len(rows)}
    data = list_feedback(workspace_id, limit=100000)
    up = sum(1 for e in data if e["rating"] == "up")
    return {"up": up, "down": len(data) - up, "total": len(data)}


# ---------------------------------------------------------------- 长期记忆

def add_memory(workspace_id: str, content: str, source: str = "manual") -> dict:
    content = content.strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    entry = {
        "id": uuid.uuid4().hex[:10],
        "workspace_id": workspace_id,
        "content": content[:500],
        "source": source,       # manual | auto（对话提炼）
        "created_at": _now(),
    }
    with _lock:
        if _use_table():
            state = _table("workspace_memory")
            # 同内容不重复记
            same = state.rows("workspace_id = ? AND content = ?", (workspace_id, entry["content"]))
            if same:
                return same[0]
            state.upsert(entry)
            # 每个工作区保留最近 MAX_MEMORY 条
            total = int(
                state.db.scalar(
                    "SELECT count(*) AS n FROM workspace_memory WHERE workspace_id = ?",
                    (workspace_id,),
                )
                or 0
            )
            if total > MAX_MEMORY:
                state.delete_where(
                    "id IN (SELECT id FROM workspace_memory WHERE workspace_id = ?"
                    " ORDER BY created_at ASC, id ASC LIMIT ?)",
                    (workspace_id, total - MAX_MEMORY),
                )
            return entry
        data = _JSON_MEMORY.read() or {}
        items = data.setdefault(workspace_id, [])
        if any(e["content"] == entry["content"] for e in items):
            return next(e for e in items if e["content"] == entry["content"])
        items.append(entry)
        data[workspace_id] = items[-MAX_MEMORY:]
        _JSON_MEMORY.write(data)
    return entry


def list_memory(workspace_id: str, q: str | None = None, limit: int = 100) -> list[dict]:
    if _use_table():
        clause = "workspace_id = ?"
        params: list = [workspace_id]
        if q and q.strip():
            clause += " AND lower(content) LIKE ?"
            params.append(f"%{q.strip().lower()}%")
        return _table("workspace_memory").rows(
            clause, tuple(params), limit=limit, order="created_at DESC"
        )
    data = _JSON_MEMORY.read() or {}
    items = data.get(workspace_id, [])
    if q and q.strip():
        ql = q.strip().lower()
        items = [e for e in items if ql in e["content"].lower()]
    return items[-limit:][::-1]


def remove_memory(workspace_id: str, memory_id: str) -> bool:
    with _lock:
        if _use_table():
            removed = _table("workspace_memory").delete_where(
                "workspace_id = ? AND id = ?", (workspace_id, memory_id)
            )
            return bool(removed)
        return _json_fallback_remove(workspace_id, memory_id)


def _json_fallback_remove(workspace_id: str, memory_id: str) -> bool:
    data = _JSON_MEMORY.read() or {}
    items = data.get(workspace_id, [])
    kept = [e for e in items if e["id"] != memory_id]
    if len(kept) == len(items):
        return False
    data[workspace_id] = kept
    _JSON_MEMORY.write(data)
    return True


def _tokens(text: str) -> set[str]:
    """极简分词：2 字滑窗 + 空格分词，够给重合度排序用。"""
    text = text.lower()
    tokens = {t for t in text.split() if len(t) >= 2}
    tokens |= {text[i:i + 2] for i in range(len(text) - 1) if text[i:i + 2].strip()}
    return tokens


def relevant_memory(workspace_id: str, query: str, top_k: int = 3) -> list[dict]:
    """按词重合度取与当前问题最相关的事实，注入系统提示词。"""
    items = list_memory(workspace_id, limit=MAX_MEMORY)
    if not items:
        return []
    qtokens = _tokens(query)
    scored = []
    for e in items:
        overlap = len(qtokens & _tokens(e["content"]))
        if overlap > 0:
            scored.append((overlap, e))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [e for _s, e in scored[:top_k]]
