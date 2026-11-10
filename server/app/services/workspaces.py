"""工作区（租户）注册表。

工作区是多租户隔离的边界：知识库、技能、专家、MCP 服务器、评测集、会话
都挂在某个工作区下，彼此不可见。开发期用 JSON 持久化，生产替换点：
PostgreSQL `workspaces` 表 + 行级安全策略。
"""

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger
from app.services.state import blob

logger = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "workspaces.json"
_STATE = blob("workspaces", STORE_PATH)

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

    def add_with_id(self, ws_id: str, name: str, description: str = "") -> Workspace:
        """指定 id 建空间：个人空间用 `p-<user_id>` 这类稳定 id，重启/改名都不变。"""
        existing = self.items.get(ws_id)
        if existing is not None:
            return existing
        ws = Workspace(
            id=ws_id,
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
        payload = {k: asdict(v) for k, v in self.items.items()}
        _STATE.write(payload)

    @classmethod
    def load(cls) -> "WorkspaceStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.items = {k: Workspace(**v) for k, v in payload.items()}
        except Exception as exc:  # noqa: BLE001 — 损坏则重建
            logger.warning("工作区数据文件损坏，已重建：%s", exc)
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


# ---------------------------------------------------------------- 邀请码
# 独立表 workspace_invites（原来是单独一个 JSON 文件）。码 → {ws, role, uses, max_uses}。
# 不放进 workspaces 表是因为邀请码生命周期短、数量可能多，别跟空间注册表搅在一起。

_INVITES = blob("workspace_invites", DATA_DIR / "workspace_invites.json")
_INVITES_LOCK = threading.Lock()


def _load_invites() -> dict:
    try:
        return _INVITES.read() or {}
    except Exception:  # noqa: BLE001 — 文件不存在或损坏则重建
        return {}


def create_invite(ws_id: str, role: str = "member", max_uses: int = 10) -> dict:
    """生成邀请码。角色只允许 member / viewer——邀请不该直接发管理员。"""
    from app.services.users import ASSIGNABLE_ROLES

    if role not in ASSIGNABLE_ROLES:
        raise ValueError(f"邀请角色只能是 {ASSIGNABLE_ROLES}")
    code = uuid.uuid4().hex[:10]
    invite = {
        "workspace_id": ws_id, "role": role, "max_uses": max_uses, "uses": 0,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _INVITES_LOCK:
        data = _load_invites()
        data[code] = invite
        _INVITES.write(data)
    return {"code": code, "ws_id": ws_id, **invite}


def list_invites(ws_id: str) -> list[dict]:
    with _INVITES_LOCK:
        data = _load_invites()
    return [
        {"code": code, "ws_id": v.get("workspace_id"), **v}
        for code, v in data.items()
        if v.get("workspace_id") == ws_id
    ]


def redeem_invite(code: str) -> dict | None:
    """核销邀请码，返回 {ws_id, role}；无效或用尽返回 None。"""
    with _INVITES_LOCK:
        data = _load_invites()
        invite = data.get(code)
        if invite is None or invite.get("uses", 0) >= invite.get("max_uses", 0):
            return None
        invite["uses"] = invite.get("uses", 0) + 1
        data[code] = invite
        _INVITES.write(data)
    ws = get_workspace_store().get(invite["workspace_id"])
    if ws is None:
        return None
    return {"ws_id": invite["workspace_id"], "role": invite["role"]}
