"""工作区成员与角色：RBAC 的「谁在哪个空间、是什么角色」。

角色是 **per-workspace** 的，不是账号上的一个全局字段：
同一个账号在自己的个人空间是 owner，在被邀请的团队空间可能只是 member。
这条关系是工作区隔离与权限判定的唯一事实来源（`users.role` 只保留
「平台管理员」这一层跨空间的平台权限）。

数据模型：
    Membership(user_id, workspace_id, role, is_personal, joined_at)

隔离规则：
- 每个账号自动获得一个个人空间 `p-<user_id>`，只有自己（和平台管理员）可见；
- 团队空间靠邀请加入，加入时定角色；
- 未加入的空间对账号完全不可见（列表里不出现，请求回落，不报错，防探测）。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "workspace_members.json"
_STATE = blob("workspace_members", STORE_PATH)

# 角色阶梯：数值越大权限越高。owner 只用于空间创建者/转让，不通过邀请发放。
ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}
MEMBER_ROLES = ("viewer", "member", "admin")

PERSONAL_PREFIX = "p-"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rank(role: str | None) -> int:
    return ROLE_RANK.get(role or "", 0)


def at_least(role: str | None, minimum: str) -> bool:
    return rank(role) >= rank(minimum)


def personal_id(user_id: str) -> str:
    return f"{PERSONAL_PREFIX}{user_id}"


def is_personal_workspace(ws_id: str) -> bool:
    """个人空间 id 前缀判定（`p-<user_id>`），用于禁止删除/接管。"""
    return (ws_id or "").startswith(PERSONAL_PREFIX)


@dataclass
class Membership:
    user_id: str
    workspace_id: str
    role: str = "member"
    is_personal: bool = False
    joined_at: str = ""


class MemberStore:
    def __init__(self) -> None:
        self.items: dict[str, Membership] = {}

    # ---------- 查询 ----------

    @staticmethod
    def _key(user_id: str, ws_id: str) -> str:
        return f"{user_id}@{ws_id}"

    def role_of(self, user_id: str, ws_id: str) -> str | None:
        item = self.items.get(self._key(user_id, ws_id))
        return item.role if item else None

    def memberships_of(self, user_id: str) -> list[Membership]:
        return [m for m in self.items.values() if m.user_id == user_id]

    def members_of(self, ws_id: str) -> list[Membership]:
        return [m for m in self.items.values() if m.workspace_id == ws_id]

    def personal_of(self, user_id: str) -> Membership | None:
        for m in self.memberships_of(user_id):
            if m.is_personal:
                return m
        return None

    # ---------- 写入 ----------

    def ensure(self, user_id: str, ws_id: str, role: str = "member", personal: bool = False) -> Membership:
        """幂等加入：已存在则只提升角色（不降级），避免重放邀请把 admin 打回 member。"""
        key = self._key(user_id, ws_id)
        item = self.items.get(key)
        if item is None:
            item = Membership(
                user_id=user_id,
                workspace_id=ws_id,
                role=role,
                is_personal=personal,
                joined_at=_now(),
            )
            self.items[key] = item
            return item
        if rank(role) > rank(item.role):
            item.role = role
        if personal:
            item.is_personal = True
        return item

    def set_role(self, user_id: str, ws_id: str, role: str) -> Membership | None:
        item = self.items.get(self._key(user_id, ws_id))
        if item is None:
            return None
        item.role = role
        return item

    def remove(self, user_id: str, ws_id: str) -> bool:
        return self.items.pop(self._key(user_id, ws_id), None) is not None

    def drop_workspace(self, ws_id: str) -> int:
        """工作区被删除时清掉全部成员关系。"""
        keys = [k for k, m in self.items.items() if m.workspace_id == ws_id]
        for k in keys:
            self.items.pop(k, None)
        return len(keys)

    # ---------- 持久化 ----------

    def save(self) -> None:
        _STATE.write({k: asdict(v) for k, v in self.items.items()})

    @classmethod
    def load(cls) -> "MemberStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.items = {k: Membership(**v) for k, v in payload.items()}
        except Exception:  # noqa: BLE001 — 损坏则重建，迁移脚本会补回关系
            store.items = {}
        return store


_store: MemberStore | None = None
_lock = threading.Lock()


def get_member_store() -> MemberStore:
    global _store
    with _lock:
        if _store is None:
            _store = MemberStore.load()
        return _store


def ensure_personal_space(user) -> Membership:
    """保证账号有个人空间：不存在就建空间 + owner 关系。

    个人空间 id 稳定为 `p-<user_id>`，显示名随昵称变化只在创建时确定
    （改名不会悄悄改显示名，避免用户认知错位）。
    """
    from app.services import workspaces

    store = get_member_store()
    ws_id = personal_id(user.id)
    registry = workspaces.get_workspace_store()
    existed = store.personal_of(user.id)
    if existed is not None and registry.get(ws_id) is not None:
        return existed
    if registry.get(ws_id) is None:
        label = (getattr(user, "display_name", "") or user.username or "").strip()
        registry.add_with_id(ws_id, f"{label} 的空间" if label else "个人空间", "仅自己可见的私人空间")
        registry.save()
    membership = store.ensure(user.id, ws_id, role="owner", personal=True)
    store.save()
    sync_user(user)
    return membership


def sync_user(user) -> None:
    """把成员关系的实时结果刷到 `user.workspaces`（内存投影，不落库）。

    归属的唯一事实来源是 workspace_members；`user.workspaces` 只是给旧逻辑
    与旧界面用的投影，改完成员关系后调一下这里，避免两边说的不一样。
    """
    if user is None:
        return
    user.workspaces = [m.workspace_id for m in get_member_store().memberships_of(user.id)]


def migrate(users_store=None, registry=None) -> dict:
    """把存量 `user.workspaces` 列表迁移成成员关系，并给每个账号补个人空间。

    幂等：可重复执行。存量账号保留原有归属（原本就在 default 的继续在），
    只是从此有了自己的空间；角色沿用它当时的全局角色映射到空间角色
    （owner/admin → admin，viewer → viewer，其余 → member；个人空间恒 owner）。
    """
    from app.services import users as users_mod
    from app.services import workspaces

    users_store = users_store or users_mod.get_user_store()
    registry = registry or workspaces.get_workspace_store()
    store = get_member_store()

    role_map = {"owner": "owner", "admin": "admin", "member": "member", "viewer": "viewer"}
    created = 0
    for user in users_store.list_all():
        if user.disabled:
            continue
        for ws_id in user.workspaces or []:
            if registry.get(ws_id) is None:
                continue
            before = store.role_of(user.id, ws_id)
            store.ensure(user.id, ws_id, role_map.get(user.role, "member"))
            if before is None:
                created += 1
        if store.personal_of(user.id) is None:
            ensure_personal_space(user)
            created += 1
        sync_user(user)
    store.save()
    return {"memberships": len(store.items), "created": created}


def workspace_ids_of(user) -> list[str]:
    """账号真正可见的空间（按注册顺序）。

    个人空间是绝对私有边界：平台管理员也不能进别人的个人空间，只能进
    自己的 + 所有「团队空间」。这是「每个用户有隔离」的硬约束，不是提示。
    """
    from app.services import workspaces

    registry = workspaces.get_workspace_store()
    existing = [w.id for w in registry.list()]
    own_personal = personal_id(getattr(user, "id", ""))
    if getattr(user, "is_admin", False):
        return [
            w for w in existing
            if not is_personal_workspace(w) or w == own_personal
        ]
    mine = [m.workspace_id for m in get_member_store().memberships_of(user.id)]
    return [w for w in existing if w in mine]


def holds_personal_space(ws_id: str, user_id: str) -> bool:
    """该空间是否是别人的个人空间（调用方用于拒绝跨人访问）。"""
    return is_personal_workspace(ws_id) and ws_id != personal_id(user_id)
