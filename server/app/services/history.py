"""会话与智能体运行历史门面。

对外保持 v0.1 的 `HistoryStore` 方法签名（`upsert_conversation` / `add_agent_run` /
`list_session_runs` ...），内部按 `settings.store_backend` 委派给 JSON 或 SQL 仓储：
- json   → 全量内存 + 单文件（兼容 v0.1 数据，无数据库时的降级路径）
- sqlite → 单机事务（WAL），本地开发默认
- postgres → 生产

`conversations` / `agent_runs` 属性仍然可用（旧脚本会直接读），但它们是只读快照，
热路径（列表、详情）必须走方法，否则 SQL 后端等于把全表读进内存。
"""

from __future__ import annotations

import threading
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.models import AgentRun, Conversation, StoredMessage
from app.db.schema import migrate
from app.services.repositories.history import (
    HistoryRepository,
    JsonHistoryRepository,
    SqlHistoryRepository,
)

__all__ = [
    "AgentRun",
    "Conversation",
    "HistoryStore",
    "StoredMessage",
    "get_history_store",
    "reset_history_store",
]

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "history_store.json"


class HistoryStore:
    def __init__(self, repo: HistoryRepository) -> None:
        self.repo = repo

    # ---------- 兼容属性（只读快照） ----------
    @property
    def conversations(self) -> dict[str, Conversation]:
        return {c.id: c for c in self.repo.list_conversations()}

    @property
    def agent_runs(self) -> dict[str, AgentRun]:
        return {r.id: r for r in self.repo.list_agent_runs()}

    # ---------- 会话 ----------
    def upsert_conversation(
        self, conversation_id: str, user_text: str, assistant_text: str, title: str,
        workspace_id: str = "default",
    ) -> Conversation:
        conv = self.repo.upsert_conversation(
            conversation_id, user_text, assistant_text, title, workspace_id,
        )
        self.repo.save()
        return conv

    def list_conversations(self, workspace_id: str | None = None) -> list[Conversation]:
        return self.repo.list_conversations(workspace_id)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self.repo.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        ok = self.repo.delete_conversation(conversation_id)
        self.repo.save()
        return ok

    # ---------- 智能体运行 ----------
    def add_agent_run(
        self, task: str, tool_ids: list[str], steps: list[dict], answer: str, status: str,
        workspace_id: str = "default", project: str = "", artifacts: list[dict] | None = None,
        session_id: str = "", turn: int = 1,
    ) -> AgentRun:
        run = self.repo.add_agent_run(
            task, tool_ids, steps, answer, status, workspace_id, project, artifacts,
            session_id, turn,
        )
        self.repo.save()
        return run

    def list_agent_runs(self, workspace_id: str | None = None) -> list[AgentRun]:
        return self.repo.list_agent_runs(workspace_id)

    def get_agent_run(self, run_id: str) -> AgentRun | None:
        return self.repo.get_agent_run(run_id)

    def delete_agent_run(self, run_id: str) -> bool:
        ok = self.repo.delete_agent_run(run_id)
        self.repo.save()
        return ok

    @staticmethod
    def session_key(run: AgentRun) -> str:
        """会话 id：老数据没有 session_id 时用它自己的 id。"""
        return run.session_id or run.id

    def list_session_runs(self, session_id: str, workspace_id: str | None = None) -> list[AgentRun]:
        """取一个会话的全部轮次，按时间正序（含把自身 id 当会话 id 的老数据）。"""
        return self.repo.list_session_runs(session_id, workspace_id)

    def delete_session(self, session_id: str, workspace_id: str | None = None) -> int:
        """删除整个会话（所有轮次），返回删除条数。"""
        deleted = self.repo.delete_session(session_id, workspace_id)
        self.repo.save()
        return deleted

    # ---------- 持久化 ----------
    def save(self) -> None:
        self.repo.save()


_store: HistoryStore | None = None
_lock = threading.Lock()


def _build_repo() -> HistoryRepository:
    backend = get_settings().store_backend
    if backend in ("sqlite", "postgres"):
        db = get_db()
        migrate(db)
        return SqlHistoryRepository(db)
    return JsonHistoryRepository(STORE_PATH)


def get_history_store() -> HistoryStore:
    global _store
    with _lock:
        if _store is None:
            _store = HistoryStore(_build_repo())
        return _store


def reset_history_store() -> None:
    """测试用：换后端或换库后重新构建。"""
    global _store
    with _lock:
        _store = None
