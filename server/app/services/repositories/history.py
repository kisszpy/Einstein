"""会话仓储：同一套语义的 JSON / SQL 两种实现。

上层 `app.services.history.HistoryStore` 只做转发，因此切换 `store_backend`
不需要改路由。语义约定（两种实现必须一致）：

1. `upsert_conversation` 一次写入「用户问 + 助手答」两条消息，会话不存在则创建。
2. `message_count` 只在列表里填，详情里的 `messages` 才是全量。
3. `list_session_runs` 同时接受「会话 id」与「老数据的 run id」——
   v0.1 的任务没有 session_id，此时 run 自己的 id 就是会话 id。
4. 排序稳定：会话按 updated_at 倒序；run 按 created_at 倒序；会话内轮次按 (created_at, turn) 正序。
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Protocol

from app.db.models import AgentRun, Conversation, StoredMessage


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------- 协议
class HistoryRepository(Protocol):
    def upsert_conversation(
        self, conversation_id: str, user_text: str, assistant_text: str, title: str,
        workspace_id: str = "default", owner_id: str = "",
    ) -> Conversation: ...
    def list_conversations(
        self, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[Conversation]: ...
    def rename_conversation(self, conversation_id: str, title: str) -> Conversation | None: ...
    def set_conversation_flags(
        self, conversation_id: str, *, archived: bool | None = None, pinned: bool | None = None,
    ) -> Conversation | None: ...
    def search_conversations(
        self, workspace_id: str, query: str, include_archived: bool = False,
        owner_id: str | None = None,
    ) -> list[Conversation]: ...
    def get_conversation(self, conversation_id: str) -> Conversation | None: ...
    def delete_conversation(self, conversation_id: str) -> bool: ...
    def add_agent_run(
        self, task: str, tool_ids: list[str], steps: list[dict], answer: str, status: str,
        workspace_id: str = "default", project: str = "", artifacts: list[dict] | None = None,
        session_id: str = "", turn: int = 1, owner_id: str = "",
    ) -> AgentRun: ...
    def list_agent_runs(
        self, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[AgentRun]: ...
    def get_agent_run(self, run_id: str) -> AgentRun | None: ...
    def delete_agent_run(self, run_id: str) -> bool: ...
    def list_session_runs(
        self, session_id: str, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[AgentRun]: ...
    def delete_session(
        self, session_id: str, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> int: ...
    def save(self) -> None: ...


# ---------------------------------------------------------------- JSON 实现
class JsonHistoryRepository:
    """v0.1 行为：全量内存 + 单文件 JSON（原子替换）。

    保留它，一是给无数据库环境兜底，二是迁移期做对照。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conversations: dict[str, Conversation] = {}
        self.agent_runs: dict[str, AgentRun] = {}
        self._lock = threading.RLock()
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 损坏则重建，与 v0.1 一致
            return
        conv_fields = {f.name for f in fields(Conversation)}
        run_fields = {f.name for f in fields(AgentRun)}
        msg_fields = {f.name for f in fields(StoredMessage)}

        for key, raw in (payload.get("conversations") or {}).items():
            data = {k: v for k, v in raw.items() if k in conv_fields}
            messages = [
                StoredMessage(**{k: v for k, v in m.items() if k in msg_fields})
                for m in (raw.get("messages") or [])
            ]
            data["messages"] = messages
            data["message_count"] = len(messages)
            self.conversations[key] = Conversation(**data)
        for key, raw in (payload.get("agent_runs") or {}).items():
            self.agent_runs[key] = AgentRun(**{k: v for k, v in raw.items() if k in run_fields})

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "conversations": {k: asdict(v) for k, v in self.conversations.items()},
                "agent_runs": {k: asdict(v) for k, v in self.agent_runs.items()},
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    # ---- 会话 ----
    def upsert_conversation(
        self, conversation_id: str, user_text: str, assistant_text: str, title: str,
        workspace_id: str = "default", owner_id: str = "",
    ) -> Conversation:
        now = _now()
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if conv is None:
                conv = Conversation(
                    id=conversation_id, title=title, created_at=now, updated_at=now,
                    workspace_id=workspace_id, owner_id=owner_id,
                )
                self.conversations[conversation_id] = conv
            conv.messages.append(StoredMessage("user", user_text, now))
            conv.messages.append(StoredMessage("assistant", assistant_text, now))
            conv.message_count = len(conv.messages)
            conv.updated_at = now
            return conv

    def list_conversations(
        self, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[Conversation]:
        rows = list(self.conversations.values())
        if workspace_id:
            rows = [c for c in rows if c.workspace_id == workspace_id]
        if owner_id is not None:
            rows = [c for c in rows if c.owner_id == owner_id]
        # 列表只给计数：返回副本并清空 messages，保证与 SQL 实现语义一致
        # （否则长会话列表会把全部正文带进列表接口）
        out = [replace(c, messages=[], message_count=len(c.messages)) for c in rows]
        return sorted(out, key=lambda c: c.updated_at, reverse=True)

    def rename_conversation(self, conversation_id: str, title: str) -> Conversation | None:
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if conv is None:
                return None
            conv.title = title
            conv.updated_at = _now()
            self.save()
            return conv

    def set_conversation_flags(
        self, conversation_id: str, *, archived: bool | None = None, pinned: bool | None = None,
    ) -> Conversation | None:
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if conv is None:
                return None
            if archived is not None:
                conv.archived = archived
            if pinned is not None:
                conv.pinned = pinned
            self.save()
            return conv

    def search_conversations(
        self, workspace_id: str, query: str, include_archived: bool = False,
        owner_id: str | None = None,
    ) -> list[Conversation]:
        q = query.strip().lower()
        hits: list[Conversation] = []
        with self._lock:
            for conv in self.conversations.values():
                if workspace_id and conv.workspace_id != workspace_id:
                    continue
                if owner_id is not None and conv.owner_id != owner_id:
                    continue
                if conv.archived and not include_archived:
                    continue
                in_title = q in conv.title.lower()
                in_msg = any(q in m.content.lower() for m in conv.messages)
                if in_title or in_msg:
                    hits.append(replace(conv, messages=[], message_count=len(conv.messages)))
        return sorted(hits, key=lambda c: (not c.pinned, c.updated_at), reverse=True)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock:
            return self.conversations.pop(conversation_id, None) is not None

    # ---- 智能体运行 ----
    def add_agent_run(
        self, task: str, tool_ids: list[str], steps: list[dict], answer: str, status: str,
        workspace_id: str = "default", project: str = "", artifacts: list[dict] | None = None,
        session_id: str = "", turn: int = 1, owner_id: str = "",
    ) -> AgentRun:
        run = AgentRun(
            id=uuid.uuid4().hex[:12],
            task=task,
            tool_ids=tool_ids,
            steps=steps,
            answer=answer,
            status=status,
            created_at=_now(),
            workspace_id=workspace_id,
            project=project,
            artifacts=artifacts or [],
            session_id=session_id,
            turn=turn,
            owner_id=owner_id,
        )
        with self._lock:
            self.agent_runs[run.id] = run
        return run

    def list_agent_runs(
        self, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[AgentRun]:
        rows = list(self.agent_runs.values())
        if workspace_id:
            rows = [r for r in rows if r.workspace_id == workspace_id]
        if owner_id is not None:
            rows = [r for r in rows if r.owner_id == owner_id]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    def get_agent_run(self, run_id: str) -> AgentRun | None:
        return self.agent_runs.get(run_id)

    def delete_agent_run(self, run_id: str) -> bool:
        with self._lock:
            return self.agent_runs.pop(run_id, None) is not None

    def list_session_runs(
        self, session_id: str, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[AgentRun]:
        if not session_id:
            return []
        rows = [
            r for r in self.agent_runs.values()
            if r.id == session_id or (r.session_id or r.id) == session_id
        ]
        if workspace_id:
            rows = [r for r in rows if r.workspace_id == workspace_id]
        if owner_id is not None:
            rows = [r for r in rows if r.owner_id == owner_id]
        return sorted(rows, key=lambda r: (r.created_at, r.turn))

    def delete_session(
        self, session_id: str, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> int:
        targets = [r.id for r in self.list_session_runs(session_id, workspace_id, owner_id)]
        with self._lock:
            for rid in targets:
                self.agent_runs.pop(rid, None)
        return len(targets)


# ---------------------------------------------------------------- SQL 实现
CONV_COLUMNS = "id, title, workspace_id, created_at, updated_at, archived, pinned, owner_id"
RUN_COLUMNS = (
    "id, session_id, turn, task, tool_ids, steps, answer, status,"
    " workspace_id, project, artifacts, created_at, owner_id"
)


def _json_list(raw: str | None, fallback: list) -> list:
    if not raw:
        return list(fallback)
    try:
        value = json.loads(raw)
    except Exception:  # noqa: BLE001 — 脏数据不该让整页 500
        return list(fallback)
    return value if isinstance(value, list) else list(fallback)


class SqlHistoryRepository:
    def __init__(self, db) -> None:  # noqa: ANN001 — Database
        self.db = db

    # ---- 行映射 ----
    @staticmethod
    def _conv(row: dict) -> Conversation:
        return Conversation(
            id=row["id"], title=row["title"] or "", created_at=row["created_at"],
            updated_at=row["updated_at"], workspace_id=row["workspace_id"] or "default",
            message_count=int(row.get("message_count") or 0),
            archived=bool(row.get("archived")), pinned=bool(row.get("pinned")),
            owner_id=row.get("owner_id") or "",
        )

    @staticmethod
    def _msg(row: dict) -> StoredMessage:
        return StoredMessage(role=row["role"], content=row["content"], created_at=row["created_at"])

    @staticmethod
    def _run(row: dict) -> AgentRun:
        return AgentRun(
            id=row["id"], task=row["task"] or "", tool_ids=_json_list(row["tool_ids"], []),
            steps=_json_list(row["steps"], []), answer=row["answer"] or "", status=row["status"],
            created_at=row["created_at"], workspace_id=row["workspace_id"] or "default",
            project=row["project"] or "", artifacts=_json_list(row["artifacts"], []),
            session_id=row["session_id"] or "", turn=int(row["turn"] or 1),
            owner_id=row.get("owner_id") or "",
        )

    # ---- 会话 ----
    def upsert_conversation(
        self, conversation_id: str, user_text: str, assistant_text: str, title: str,
        workspace_id: str = "default", owner_id: str = "",
    ) -> Conversation:
        now = _now()
        with self.db.transaction():
            exists = self.db.query_one(
                "SELECT created_at FROM conversations WHERE id = ?", (conversation_id,),
            )
            if exists is None:
                self.db.execute(
                    "INSERT INTO conversations (id, title, workspace_id, created_at, updated_at, owner_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (conversation_id, title, workspace_id, now, now, owner_id),
                )
                created_at = now
            else:
                self.db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id),
                )
                created_at = exists["created_at"]
            base = self.db.scalar(
                "SELECT COALESCE(max(seq), -1) AS m FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            start = int(base if base is not None else -1) + 1
            self.db.executemany(
                "INSERT INTO messages (id, conversation_id, role, content, seq, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (_new_id(), conversation_id, "user", user_text, start, now),
                    (_new_id(), conversation_id, "assistant", assistant_text, start + 1, now),
                ],
            )
            total = self.db.scalar(
                "SELECT count(*) AS n FROM messages WHERE conversation_id = ?", (conversation_id,),
            )
        return Conversation(
            id=conversation_id, title=title, created_at=created_at, updated_at=now,
            workspace_id=workspace_id, message_count=int(total or 0), owner_id=owner_id,
        )

    def list_conversations(
        self, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[Conversation]:
        where, params = [], []
        if workspace_id:
            where.append("c.workspace_id = ?")
            params.append(workspace_id)
        if owner_id is not None:
            where.append("c.owner_id = ?")
            params.append(owner_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.query(
            f"SELECT c.id, c.title, c.workspace_id, c.created_at, c.updated_at,"
            f" c.archived, c.pinned, c.owner_id,"
            f" (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count"
            f" FROM conversations c{clause} ORDER BY c.pinned DESC, c.updated_at DESC",
            params,
        )
        return [self._conv(r) for r in rows]

    def rename_conversation(self, conversation_id: str, title: str) -> Conversation | None:
        affected = self.db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )
        return self.get_conversation(conversation_id) if affected else None

    def set_conversation_flags(
        self, conversation_id: str, *, archived: bool | None = None, pinned: bool | None = None,
    ) -> Conversation | None:
        sets, params = [], []
        if archived is not None:
            sets.append("archived = ?")
            params.append(1 if archived else 0)
        if pinned is not None:
            sets.append("pinned = ?")
            params.append(1 if pinned else 0)
        if not sets:
            return self.get_conversation(conversation_id)
        self.db.execute(
            f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?",
            (*params, conversation_id),
        )
        return self.get_conversation(conversation_id)

    def search_conversations(
        self, workspace_id: str, query: str, include_archived: bool = False,
        owner_id: str | None = None,
    ) -> list[Conversation]:
        q = f"%{query.strip()}%"
        if not query.strip():
            return []
        arch = "" if include_archived else " AND c.archived = 0"
        owned = " AND c.owner_id = ?" if owner_id is not None else ""
        params: list = [workspace_id, q, q]
        if owner_id is not None:
            params.append(owner_id)
        rows = self.db.query(
            "SELECT c.id, c.title, c.workspace_id, c.created_at, c.updated_at,"
            " c.archived, c.pinned, c.owner_id,"
            " (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count"
            " FROM conversations c WHERE c.workspace_id = ?"
            f" AND (c.title LIKE ? OR EXISTS (SELECT 1 FROM messages m"
            f" WHERE m.conversation_id = c.id AND m.content LIKE ?)){owned}{arch}"
            " ORDER BY c.pinned DESC, c.updated_at DESC",
            params,
        )
        return [self._conv(r) for r in rows]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self.db.query_one(
            f"SELECT {CONV_COLUMNS} FROM conversations WHERE id = ?", (conversation_id,),
        )
        if row is None:
            return None
        conv = self._conv(row)
        conv.messages = [
            self._msg(r)
            for r in self.db.query(
                "SELECT role, content, created_at FROM messages"
                " WHERE conversation_id = ? ORDER BY seq, created_at",
                (conversation_id,),
            )
        ]
        conv.message_count = len(conv.messages)
        return conv

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.db.transaction():
            self.db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            affected = self.db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return affected > 0

    # ---- 智能体运行 ----
    def add_agent_run(
        self, task: str, tool_ids: list[str], steps: list[dict], answer: str, status: str,
        workspace_id: str = "default", project: str = "", artifacts: list[dict] | None = None,
        session_id: str = "", turn: int = 1, owner_id: str = "",
    ) -> AgentRun:
        run = AgentRun(
            id=uuid.uuid4().hex[:12], task=task, tool_ids=list(tool_ids), steps=steps,
            answer=answer, status=status, created_at=_now(), workspace_id=workspace_id,
            project=project, artifacts=artifacts or [], session_id=session_id, turn=turn,
            owner_id=owner_id,
        )
        self.db.execute(
            "INSERT INTO agent_runs (id, session_id, turn, task, tool_ids, steps, answer,"
            " status, workspace_id, project, artifacts, created_at, owner_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id, run.session_id, run.turn, run.task,
                json.dumps(run.tool_ids, ensure_ascii=False),
                json.dumps(run.steps, ensure_ascii=False),
                run.answer, run.status, run.workspace_id, run.project,
                json.dumps(run.artifacts, ensure_ascii=False), run.created_at, run.owner_id,
            ),
        )
        return run

    def list_agent_runs(
        self, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[AgentRun]:
        where, params = [], []
        if workspace_id:
            where.append("workspace_id = ?")
            params.append(workspace_id)
        if owner_id is not None:
            where.append("owner_id = ?")
            params.append(owner_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.query(
            f"SELECT {RUN_COLUMNS} FROM agent_runs{clause} ORDER BY created_at DESC", params,
        )
        return [self._run(r) for r in rows]

    def get_agent_run(self, run_id: str) -> AgentRun | None:
        row = self.db.query_one(f"SELECT {RUN_COLUMNS} FROM agent_runs WHERE id = ?", (run_id,))
        return self._run(row) if row else None

    def delete_agent_run(self, run_id: str) -> bool:
        return self.db.execute("DELETE FROM agent_runs WHERE id = ?", (run_id,)) > 0

    def list_session_runs(
        self, session_id: str, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> list[AgentRun]:
        if not session_id:
            return []
        where = " WHERE (id = ? OR session_id = ?)"
        params: list = [session_id, session_id]
        if workspace_id:
            where += " AND workspace_id = ?"
            params.append(workspace_id)
        if owner_id is not None:
            where += " AND owner_id = ?"
            params.append(owner_id)
        rows = self.db.query(
            f"SELECT {RUN_COLUMNS} FROM agent_runs{where} ORDER BY created_at, turn", params,
        )
        return [self._run(r) for r in rows]

    def delete_session(
        self, session_id: str, workspace_id: str | None = None, owner_id: str | None = None,
    ) -> int:
        runs = self.list_session_runs(session_id, workspace_id, owner_id)
        if not runs:
            return 0
        marks = ", ".join("?" for _ in runs)
        deleted = self.db.execute(
            f"DELETE FROM agent_runs WHERE id IN ({marks})", [r.id for r in runs],
        )
        return max(deleted, 0)

    def save(self) -> None:
        """SQL 后端每次写就落库，save 保留为空操作以兼容旧调用点。"""
