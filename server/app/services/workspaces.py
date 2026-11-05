"""工作区（租户）注册表。

工作区是多租户隔离的边界：知识库、技能、专家、MCP 服务器、评测集、会话
都挂在某个工作区下，彼此不可见。开发期用 JSON 持久化，生产替换点：
PostgreSQL `workspaces` 表 + 行级安全策略。
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "workspaces.json"

DEFAULT_ID = "default"
DEFAULT_NAME = "默认工作区"


@dataclass
class Workspace:
    id: str
    name: str
    created_at: str = ""
    description: str = ""


class WorkspaceStore:
    def __init__(self) -> None:
        self.items: dict[str, Workspace] = {}

    def ensure_default(self) -> None:
        if DEFAULT_ID not in self.items:
            self.items[DEFAULT_ID] = Workspace(
                id=DEFAULT_ID,
                name=DEFAULT_NAME,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                description="系统内置工作区，历史数据归入此处",
            )

    def list(self) -> list[Workspace]:
        return sorted(self.items.values(), key=lambda w: w.created_at)

    def get(self, ws_id: str) -> Workspace | None:
        return self.items.get(ws_id)

    def add(self, name: str, description: str = "") -> Workspace:
        ws = Workspace(
            id=uuid.uuid4().hex[:10],
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            description=description,
        )
        self.items[ws.id] = ws
        return ws

    def rename(self, ws_id: str, name: str, description: str | None = None) -> Workspace | None:
        ws = self.items.get(ws_id)
        if ws is None:
            return None
        ws.name = name
        if description is not None:
            ws.description = description
        return ws

    def delete(self, ws_id: str) -> bool:
        """默认工作区不可删；其余由调用方先清理资源。"""
        if ws_id == DEFAULT_ID or ws_id not in self.items:
            return False
        del self.items[ws_id]
        return True

    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        payload = {k: asdict(v) for k, v in self.items.items()}
        STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "WorkspaceStore":
        store = cls()
        if STORE_PATH.exists():
            try:
                payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
                store.items = {k: Workspace(**v) for k, v in payload.items()}
            except Exception:  # noqa: BLE001 — 损坏则重建
                pass
        store.ensure_default()
        return store


_store: WorkspaceStore | None = None
_lock = threading.Lock()


def get_workspace_store() -> WorkspaceStore:
    global _store
    with _lock:
        if _store is None:
            _store = WorkspaceStore.load()
        return _store


def resolve(ws_id: str | None) -> str:
    """把请求里的工作区标识收敛成合法 ID：未知或缺失一律落到默认工作区。"""
    if not ws_id:
        return DEFAULT_ID
    return ws_id if get_workspace_store().get(ws_id) is not None else DEFAULT_ID
