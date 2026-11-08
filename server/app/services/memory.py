"""消息反馈（赞/踩）与长期记忆。

反馈：按会话存 {rating, reason}，清单给管理员看，可一键转成评测用例。
记忆：工作区级事实库（手工添加 + 对话结束自动提炼，自动提炼默认关），
检索按关键词重合度取 top——事实条数少，不用向量。
"""

import threading
import uuid
from datetime import datetime, timezone

from app.services.state import blob
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_FEEDBACK_STATE = blob("feedback", _DATA_DIR / "message_feedback.json")
_MEMORY_STATE = blob("memory", _DATA_DIR / "workspace_memory.json")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        data = _FEEDBACK_STATE.read() or []
        data.append(entry)
        _FEEDBACK_STATE.write(data[-5000:])
    return entry


def list_feedback(workspace_id: str | None = None, rating: str | None = None,
                  limit: int = 200) -> list[dict]:
    data = _FEEDBACK_STATE.read() or []
    out = [e for e in data
           if (workspace_id is None or e.get("workspace_id") == workspace_id)
           and (rating is None or e.get("rating") == rating)]
    return out[-limit:][::-1]


def feedback_summary(workspace_id: str | None = None) -> dict:
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
        data = _MEMORY_STATE.read() or {}
        items = data.setdefault(workspace_id, [])
        # 同内容不重复记
        if any(e["content"] == entry["content"] for e in items):
            return next(e for e in items if e["content"] == entry["content"])
        items.append(entry)
        data[workspace_id] = items[-500:]
        _MEMORY_STATE.write(data)
    return entry


def list_memory(workspace_id: str, q: str | None = None, limit: int = 100) -> list[dict]:
    data = _MEMORY_STATE.read() or {}
    items = data.get(workspace_id, [])
    if q and q.strip():
        ql = q.strip().lower()
        items = [e for e in items if ql in e["content"].lower()]
    return items[-limit:][::-1]


def remove_memory(workspace_id: str, memory_id: str) -> bool:
    with _lock:
        data = _MEMORY_STATE.read() or {}
        items = data.get(workspace_id, [])
        kept = [e for e in items if e["id"] != memory_id]
        if len(kept) == len(items):
            return False
        data[workspace_id] = kept
        _MEMORY_STATE.write(data)
    return True


def _tokens(text: str) -> set[str]:
    """极简分词：2 字滑窗 + 空格分词，够给重合度排序用。"""
    text = text.lower()
    tokens = {t for t in text.split() if len(t) >= 2}
    tokens |= {text[i:i + 2] for i in range(len(text) - 1) if text[i:i + 2].strip()}
    return tokens


def relevant_memory(workspace_id: str, query: str, top_k: int = 3) -> list[dict]:
    """按词重合度取与当前问题最相关的事实，注入系统提示词。"""
    items = list_memory(workspace_id, limit=500)
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
