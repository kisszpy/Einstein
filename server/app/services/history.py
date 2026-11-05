"""会话与智能体运行历史：开发期 JSON 持久化，与 KB 同一适配器模式。

生产替换点：PostgreSQL（conversations / messages / agent_runs 表）。
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "history_store.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StoredMessage:
    role: str
    content: str
    created_at: str


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[StoredMessage] = field(default_factory=list)
    workspace_id: str = "default"


@dataclass
class AgentRun:
    id: str
    task: str
    tool_ids: list[str]
    steps: list[dict]
    answer: str
    status: str  # ok | error
    created_at: str
    workspace_id: str = "default"


@dataclass
class HistoryStore:
    conversations: dict[str, Conversation] = field(default_factory=dict)
    agent_runs: dict[str, AgentRun] = field(default_factory=dict)

    # ---- 会话 ----
    def upsert_conversation(
        self, conversation_id: str, user_text: str, assistant_text: str, title: str,
        workspace_id: str = "default",
    ) -> Conversation:
        now = _now()
        conv = self.conversations.get(conversation_id)
        if conv is None:
            conv = Conversation(
                id=conversation_id, title=title, created_at=now, updated_at=now,
                workspace_id=workspace_id,
            )
            self.conversations[conversation_id] = conv
        conv.messages.append(StoredMessage("user", user_text, now))
        conv.messages.append(StoredMessage("assistant", assistant_text, now))
        conv.updated_at = now
        return conv

    def list_conversations(self, workspace_id: str | None = None) -> list[Conversation]:
        rows = list(self.conversations.values())
        if workspace_id:
            rows = [c for c in rows if c.workspace_id == workspace_id]
        return sorted(rows, key=lambda c: c.updated_at, reverse=True)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        return self.conversations.pop(conversation_id, None) is not None

    # ---- 智能体运行 ----
    def add_agent_run(
        self, task: str, tool_ids: list[str], steps: list[dict], answer: str, status: str,
        workspace_id: str = "default",
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
        )
        self.agent_runs[run.id] = run
        return run

    def list_agent_runs(self, workspace_id: str | None = None) -> list[AgentRun]:
        rows = list(self.agent_runs.values())
        if workspace_id:
            rows = [r for r in rows if r.workspace_id == workspace_id]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    # ---- 持久化 ----
    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        payload = {
            "conversations": {k: asdict(v) for k, v in self.conversations.items()},
            "agent_runs": {k: asdict(v) for k, v in self.agent_runs.items()},
        }
        STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "HistoryStore":
        if not STORE_PATH.exists():
            return cls()
        try:
            payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            store = cls()
            store.conversations = {
                k: Conversation(**v) for k, v in payload.get("conversations", {}).items()
            }
            store.agent_runs = {
                k: AgentRun(**v) for k, v in payload.get("agent_runs", {}).items()
            }
            return store
        except Exception:  # noqa: BLE001 — 损坏则重建
            return cls()


_store: HistoryStore | None = None
_lock = threading.Lock()


def get_history_store() -> HistoryStore:
    global _store
    with _lock:
        if _store is None:
            _store = HistoryStore.load()
        return _store
