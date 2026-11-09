"""知识库级授权："这个库只给财务组"。

三档可见性（private / workspace / shared）回答的是"大体给谁看"，回答不了
"只开给某几个人"。授权表是它的**叠加层**：不改变可见性语义，只在它之上
额外放行，并且区分读与写。

为什么读/写要分开：能检索一个库和能往里传文档是两件事。审计、财务这类场景
里"只读"是常态——给全组开只读，比让所有人往制度库里塞文件安全得多。

权限判定顺序（先到先得，宽在前）：

    owner / 预置('')      → write
    workspace 且同空间    → read（同空间默认可读，与可见性语义一致）
    shared               → read（跨空间只读）
    显式授权             → 按授权档位（read / write）

`write` 只在 owner、预置、或显式 write 授权时成立——**同事不会因为我设了
"工作区可见"就获得写权限**。旧实现里 `owns()` 同时被读写两处使用，等于把
"能看"当成了"能改"，这里明确拆开。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import KB_GRANTS
from app.services.state import JsonState

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON = JsonState(_DATA_DIR / "kb_grants.json")
_lock = threading.Lock()

ROLES = ("read", "write")
_RANK = {"read": 1, "write": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table() -> TableState:
    db = get_db()
    migrate(db)
    return TableState(KB_GRANTS, db)


def _all() -> list[dict]:
    if _use_table():
        return _table().rows()
    return [r for r in (_JSON.read() or []) if isinstance(r, dict)]


def role_of(kb_id: str, user_id: str) -> str | None:
    if not kb_id or not user_id:
        return None
    for row in _all():
        if row.get("kb_id") == kb_id and row.get("user_id") == user_id:
            return row.get("role") or "read"
    return None


def granted_kb_ids(user_id: str) -> set[str]:
    if not user_id:
        return set()
    return {r["kb_id"] for r in _all() if r.get("user_id") == user_id and r.get("kb_id")}


def list_grants(kb_id: str) -> list[dict]:
    return [r for r in _all() if r.get("kb_id") == kb_id]


def grant(
    *, kb_id: str, workspace_id: str, user_id: str,
    role: str = "read", granted_by: str = "",
) -> dict:
    if role not in ROLES:
        raise ValueError(f"role 只支持 {'/'.join(ROLES)}")
    if not user_id:
        raise ValueError("user_id 不能为空")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "kb_id": kb_id,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "role": role,
        "granted_by": granted_by,
        "created_at": _now(),
    }
    with _lock:
        existing = [r for r in _all() if r.get("kb_id") == kb_id and r.get("user_id") == user_id]
        if existing:
            # 重复授权 = 改档位，而不是插一条新的：复合主键决定了只能有一条，
            # 静默插入会在 SQL 后端报主键冲突。
            entry["id"] = existing[0].get("id") or entry["id"]
            entry["created_at"] = existing[0].get("created_at") or entry["created_at"]
        if _use_table():
            _table().upsert(entry)
        else:
            rows = [
                r for r in (_JSON.read() or [])
                if not (r.get("kb_id") == kb_id and r.get("user_id") == user_id)
            ]
            rows.append(entry)
            _JSON.write(rows)
    return entry


def revoke(kb_id: str, user_id: str) -> bool:
    with _lock:
        if _use_table():
            return bool(
                _table().delete_where("kb_id = ? AND user_id = ?", (kb_id, user_id))
            )
        rows = list(_JSON.read() or [])
        kept = [
            r for r in rows
            if not (r.get("kb_id") == kb_id and r.get("user_id") == user_id)
        ]
        if len(kept) == len(rows):
            return False
        _JSON.write(kept)
        return True


def access(store, kb_id: str, workspace_id: str, user_id: str) -> str | None:
    """返回 "read" / "write" / None。None 一律按"不存在"处理，不泄露存在性。"""
    kb = store.get_kb(kb_id)
    if kb is None:
        return None

    granted = role_of(kb_id, user_id)
    # 预置库（owner_id=''）全员可读，但没人"拥有"它——改内容要靠显式 write 授权，
    # 否则等于任何一个登录用户都能往公共库里塞文档。
    if kb.owner_id == "":
        return _best("read", granted)
    if kb.owner_id == user_id:
        return "write"

    same_ws = kb.workspace_id == workspace_id
    if same_ws and kb.visibility == "workspace":
        return _best("read", granted)
    if kb.visibility == "shared":
        # 跨空间共享只读：共享出去是为了被引用，不是为了被改写
        return _best("read", granted)
    return granted


def _best(base: str, granted: str | None) -> str:
    if granted and _RANK.get(granted, 0) > _RANK.get(base, 0):
        return granted
    return base


def grant_payload(row: dict) -> dict:
    return {
        "id": row.get("id", ""),
        "kb_id": row.get("kb_id", ""),
        "user_id": row.get("user_id", ""),
        "role": row.get("role", "read"),
        "granted_by": row.get("granted_by", ""),
        "created_at": row.get("created_at", ""),
    }
