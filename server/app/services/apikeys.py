"""API Key 签发与校验（开放 API 用）。

明文只在签发时返回一次，库里只存 SHA-256 摘要——库被拖走也拿不到可用凭据。
生产替换点：PostgreSQL `api_keys` 表 + 定期轮换策略。
"""

import hashlib
import json
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob

from app.services.workspaces import DEFAULT_ID

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "api_keys.json"
_STATE = blob("apikeys", STORE_PATH)

KEY_PREFIX = "sk-"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class ApiKey:
    id: str
    name: str
    workspace_id: str
    key_hash: str
    prefix: str
    scopes: list[str] = field(default_factory=lambda: ["*"])
    created_at: str = ""
    last_used_at: str | None = None
    revoked: bool = False
    call_count: int = 0
    # 签发者：开放 API 按它收敛可见资源（'' = 平台预置密钥，只能碰预置资源）
    owner_id: str = ""

    def to_public(self) -> dict:
        d = asdict(self)
        d.pop("key_hash", None)  # 摘要也不外泄
        return d


class ApiKeyStore:
    def __init__(self) -> None:
        self.items: dict[str, ApiKey] = {}

    def list_keys(self, workspace_id: str | None = None) -> list["ApiKey"]:
        rows = list(self.items.values())
        if workspace_id:
            rows = [k for k in rows if k.workspace_id == workspace_id]
        return sorted(rows, key=lambda k: k.created_at, reverse=True)

    def get(self, key_id: str) -> ApiKey | None:
        return self.items.get(key_id)

    def issue(self, name: str, workspace_id: str = DEFAULT_ID,
              scopes: list[str] | None = None, owner_id: str = "") -> tuple[ApiKey, str]:
        token = KEY_PREFIX + secrets.token_hex(20)
        key = ApiKey(
            id=uuid.uuid4().hex[:12],
            name=name or "未命名密钥",
            workspace_id=workspace_id,
            key_hash=_hash(token),
            prefix=token[:10] + "…",
            scopes=scopes or ["*"],
            created_at=_now(),
            owner_id=owner_id,
        )
        self.items[key.id] = key
        return key, token

    def revoke(self, key_id: str) -> bool:
        key = self.items.get(key_id)
        if key is None:
            return False
        key.revoked = True
        return True

    def delete(self, key_id: str) -> bool:
        return self.items.pop(key_id, None) is not None

    def verify(self, token: str) -> ApiKey | None:
        """按摘要比对；吊销的密钥视为无效。命中则顺手更新使用统计。"""
        if not token or not token.startswith(KEY_PREFIX):
            return None
        digest = _hash(token)
        for key in self.items.values():
            if key.key_hash == digest and not key.revoked:
                key.last_used_at = _now()
                key.call_count += 1
                return key
        return None

    def save(self) -> None:
        payload = {k: asdict(v) for k, v in self.items.items()}
        _STATE.write(payload)

    @classmethod
    def load(cls) -> "ApiKeyStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.items = {k: ApiKey(**v) for k, v in payload.items()}
        except Exception:  # noqa: BLE001 — 损坏则重建
            pass
        return store


_store: ApiKeyStore | None = None
_lock = threading.Lock()


def get_api_key_store() -> ApiKeyStore:
    global _store
    with _lock:
        if _store is None:
            _store = ApiKeyStore.load()
        return _store
