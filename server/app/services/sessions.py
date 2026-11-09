"""控制台会话令牌。

登录签发随机令牌，服务端只存 SHA-256 摘要，有效期 7 天；登出即吊销。
令牌走 `Authorization: Bearer`，与开放 API 的 `sk-` 密钥互不相通
（前者进控制台，后者只进 /v1）。

存储：`user_sessions` 表（一令牌一行，按 token_hash 主键、user_id 建索引）。
原来是整域 JSON 文件，每次签发票据都要重写全部会话。
"""

import hashlib
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.state import blob

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "sessions.json"
_STATE = blob("sessions", STORE_PATH)

TOKEN_PREFIX = "sess-"
TTL_DAYS = 7
MAX_SESSIONS = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Session:
    token_hash: str
    user_id: str
    created_at: str
    expires_at: str
    user_agent: str = ""

    def expired(self) -> bool:
        try:
            return datetime.fromisoformat(self.expires_at) <= _now()
        except ValueError:
            return True


class SessionStore:
    def __init__(self) -> None:
        self.items: dict[str, Session] = {}

    def issue(self, user_id: str, user_agent: str = "") -> tuple[str, Session]:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        session = Session(
            token_hash=_hash(token),
            user_id=user_id,
            created_at=_now().isoformat(timespec="seconds"),
            expires_at=(_now() + timedelta(days=TTL_DAYS)).isoformat(timespec="seconds"),
            user_agent=user_agent[:180],
        )
        self.items[session.token_hash] = session
        self._trim()
        return token, session

    def resolve(self, token: str) -> Session | None:
        if not token or not token.startswith(TOKEN_PREFIX):
            return None
        session = self.items.get(_hash(token))
        if session is None:
            return None
        if session.expired():
            self.items.pop(session.token_hash, None)
            return None
        return session

    def revoke(self, token: str) -> bool:
        return self.items.pop(_hash(token), None) is not None

    def revoke_user(self, user_id: str) -> int:
        hit = [h for h, s in self.items.items() if s.user_id == user_id]
        for h in hit:
            self.items.pop(h, None)
        return len(hit)

    def prune(self) -> int:
        dead = [h for h, s in self.items.items() if s.expired()]
        for h in dead:
            self.items.pop(h, None)
        return len(dead)

    def _trim(self) -> None:
        """令牌过多时丢最早过期的一批，防止文件无限增长。"""
        if len(self.items) <= MAX_SESSIONS:
            return
        rows = sorted(self.items.values(), key=lambda s: s.expires_at)
        for s in rows[: len(self.items) - MAX_SESSIONS]:
            self.items.pop(s.token_hash, None)

    def save(self) -> None:
        _STATE.write({k: asdict(v) for k, v in self.items.items()})

    @classmethod
    def load(cls) -> "SessionStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.items = {k: Session(**v) for k, v in payload.items()}
        except Exception:  # noqa: BLE001 — 损坏则视为全部下线
            store.items = {}
        store.prune()
        return store


_store: SessionStore | None = None
_lock = threading.Lock()


def get_session_store() -> SessionStore:
    global _store
    with _lock:
        if _store is None:
            _store = SessionStore.load()
        return _store
