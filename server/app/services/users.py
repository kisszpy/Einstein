"""控制台账号：用户、密码摘要、角色。

密码用 PBKDF2-HMAC-SHA256（12 万轮 + 每用户随机盐）存摘要，不落明文；
比对走 `hmac.compare_digest`，避免时序侧信道。首次启动若库为空，种一个
`admin / admin123` 的所有者账号，登录后应立刻改密码。

职责边界：这张表只存「账号本身」——身份、凭据、平台级角色。
「谁在哪个空间、是什么角色」属于 workspace_members（见 members.py），
`User.workspaces` 只是成员表的投影，不再落库，避免两处各写一份而对不上。
"""

import hashlib
import hmac
import re
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob
from app.services.workspaces import DEFAULT_ID

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "users.json"
_STATE = blob("users", STORE_PATH)

ITERATIONS = 120_000
ROLES = ("owner", "admin", "member", "viewer")
ROLE_LABEL = {"owner": "所有者", "admin": "管理员", "member": "成员", "viewer": "只读"}
# 允许在 UI 上分配的角色（owner 只能是唯一创始人，不从下拉里选）
ASSIGNABLE_ROLES = ("admin", "member", "viewer")

SEED_USERNAME = "admin"
SEED_PASSWORD = "admin123"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _memberships(user_id: str, workspace_id: str | None = None) -> list:
    """账号的成员关系（延迟导入：members 依赖本模块，模块级导入会成环）。"""
    from app.services import members

    rows = members.get_member_store().memberships_of(user_id)
    if workspace_id is None:
        return rows
    return [m for m in rows if m.workspace_id == workspace_id]


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    if not password_hash or not salt:
        return False
    try:
        candidate, _ = hash_password(password, salt)
    except ValueError:  # 盐损坏
        return False
    return hmac.compare_digest(candidate, password_hash)


@dataclass
class User:
    id: str
    username: str
    display_name: str
    # 邮箱登录用；老账号为空。同邮箱只允许一个账号（写入侧保证）
    email: str = ""
    role: str = "member"
    password_hash: str = ""
    salt: str = ""
    # 成员表的投影：读时按成员关系回填，写时不落库（权限事实来源只有 workspace_members）
    workspaces: list[str] = field(default_factory=list)
    created_at: str = ""
    last_login_at: str | None = None
    disabled: bool = False

    def to_public(self) -> dict:
        """对外视图：绝不带密码摘要与盐；归属取成员关系实时值。"""
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "role_label": ROLE_LABEL.get(self.role, self.role),
            "workspaces": [m.workspace_id for m in _memberships(self.id)],
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "disabled": self.disabled,
        }

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")

    @property
    def can_write(self) -> bool:
        """viewer 只读：不能建/改/删任何资源（对话类接口由中间件白名单放行）。"""
        return self.role != "viewer"

    def can_access(self, workspace_id: str) -> bool:
        """权限判定走成员关系：平台管理员跨空间，其余看自己在目标空间是否有角色。"""
        return self.is_admin or bool(_memberships(self.id, workspace_id))


class UserStore:
    def __init__(self) -> None:
        self.items: dict[str, User] = {}

    # ---------- 查询 ----------

    def list_all(self) -> list[User]:
        order = {r: i for i, r in enumerate(ROLES)}
        return sorted(
            self.items.values(),
            key=lambda u: (order.get(u.role, 9), u.created_at),
        )

    def get(self, user_id: str) -> User | None:
        return self.items.get(user_id)

    def by_username(self, username: str) -> User | None:
        key = username.strip().lower()
        for u in self.items.values():
            if u.username.lower() == key:
                return u
        return None

    def by_email(self, email: str) -> User | None:
        key = (email or "").strip().lower()
        if not key:
            return None
        for u in self.items.values():
            if u.email and u.email.lower() == key:
                return u
        return None

    def admins(self) -> list[User]:
        return [u for u in self.items.values() if u.is_admin and not u.disabled]

    # ---------- 写入 ----------

    def create(
        self,
        username: str,
        password: str,
        display_name: str = "",
        role: str = "member",
        workspaces: list[str] | None = None,
    ) -> User:
        username = username.strip()
        if not username:
            raise ValueError("用户名不能为空")
        if self.by_username(username) is not None:
            raise ValueError("用户名已存在")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        if role not in ROLES:
            raise ValueError(f"角色只能是 {ROLES}")
        pw_hash, salt = hash_password(password)
        user = User(
            id=uuid.uuid4().hex[:12],
            username=username,
            display_name=display_name.strip() or username,
            role=role,
            password_hash=pw_hash,
            salt=salt,
            # 新账号不再默认并入共享工作区：个人空间由路由层建（members.ensure_personal_space），
            # 共享空间只能靠邀请或管理员显式指定加入——否则所有新用户都看见同一批数据。
            workspaces=[w for w in (workspaces or []) if w],
            created_at=_now(),
        )
        self.items[user.id] = user
        return user

    def create_email_user(self, email: str, display_name: str = "") -> User:
        """邮箱首次登录即注册：随机密码占位，密码登录对这条账号自然不可用。"""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValueError("邮箱格式不正确")
        if self.by_email(email) is not None:
            raise ValueError("该邮箱已注册")
        local = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower()) or "user"
        username = local
        suffix = 1
        while self.by_username(username) is not None:
            suffix += 1
            username = f"{local}{suffix}"
        user = self.create(
            username,
            secrets.token_urlsafe(24),
            display_name=(display_name or "").strip() or local,
            role="member",
        )
        user.email = email
        return user

    def set_password(self, user: User, password: str) -> None:
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        user.password_hash, user.salt = hash_password(password)

    def update(self, user: User, **fields) -> User:
        for key in ("display_name", "role", "workspaces", "disabled"):
            if key in fields and fields[key] is not None:
                if key == "role" and fields[key] not in ROLES:
                    raise ValueError(f"角色只能是 {ROLES}")
                setattr(user, key, fields[key])
        return user

    def delete(self, user_id: str) -> bool:
        return self.items.pop(user_id, None) is not None

    # ---------- 认证 ----------

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.by_username(username)
        if user is None or user.disabled:
            return None
        if not verify_password(password, user.password_hash, user.salt):
            return None
        user.last_login_at = _now()
        return user

    # ---------- 持久化 ----------

    def save(self) -> None:
        payload = {k: asdict(v) for k, v in self.items.items()}
        _STATE.write(payload)

    @classmethod
    def load(cls) -> "UserStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.items = {k: User(**v) for k, v in payload.items()}
        except Exception:  # noqa: BLE001 — 损坏则重建
            store.items = {}
        if not store.items:
            admin = store.create(
                SEED_USERNAME,
                SEED_PASSWORD,
                display_name="平台管理员",
                role="owner",
            )
            store.save()
            # 归属不落 users 表：种子账号的默认空间成员关系直接写进成员表
            from app.services import members

            member_store = members.get_member_store()
            member_store.ensure(admin.id, DEFAULT_ID, role="owner")
            member_store.save()
        for user in store.items.values():
            user.workspaces = [m.workspace_id for m in _memberships(user.id)]
        return store


_store: UserStore | None = None
_lock = threading.Lock()


def get_user_store() -> UserStore:
    global _store
    with _lock:
        if _store is None:
            _store = UserStore.load()
        return _store
