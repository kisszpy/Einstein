"""发布与嵌入：把一段内容变成"拿着链接就能看"的只读页。

设计上的三个决定：

1. **token 即权限**。不定"访客角色"，也不给匿名用户开账号——匿名账号会污染
   成员表、用量表和审计，而分享的本质只是"能读这一份"。token 泄露的爆炸半径
   也只限于这一份内容，撤销即失效。
2. **默认不过期、但要能限时**。内部资料外发常带时效（评审期三天），
   `expires_at` 留空表示长期有效；过期判定放在读取时做，不靠定时任务清理，
   否则一旦清理任务停摆，过期链接仍然是活的。
3. **知识库分享默认不许追问**。允许匿名提问等于让任何人花你的 token 钱，
   必须显式打开；打开后仍按 IP 限流（见 routes/public.py）。
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import SHARES
from app.services.state import JsonState

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON = JsonState(_DATA_DIR / "shares.json")
_lock = threading.Lock()

KINDS = ("conversation", "kb")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table() -> TableState:
    db = get_db()
    migrate(db)
    return TableState(SHARES, db)


def _all() -> list[dict]:
    if _use_table():
        return _table().rows()
    return [r for r in (_JSON.read() or []) if isinstance(r, dict)]


def _save_all(rows: list[dict]) -> None:
    if _use_table():
        _table().write({r["id"]: r for r in rows})
    else:
        _JSON.write(rows)


def _expired(share: dict, now: datetime | None = None) -> bool:
    raw = (share.get("expires_at") or "").strip()
    if not raw:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        deadline = datetime.fromisoformat(raw)
    except ValueError:
        # 时间格式脏了宁可当作过期：分享是"对外契约"，不确定就该拦
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now >= deadline


def is_live(share: dict | None) -> bool:
    if not share:
        return False
    return not share.get("revoked") and not _expired(share)


def create_share(
    *, workspace_id: str, owner_id: str, kind: str, target_id: str,
    title: str = "", allow_ask: bool = False, expires_days: int = 0,
    note: str = "",
) -> dict:
    """新建一条分享。`expires_days<=0` 表示不过期。"""
    if kind not in KINDS:
        raise ValueError(f"kind 只能是 {'/'.join(KINDS)}")
    if not (target_id or "").strip():
        raise ValueError("缺少要分享的对象")
    if kind == "conversation":
        allow_ask = False   # 会话是静态文本，追问没有落点
    expires_at = ""
    if expires_days and expires_days > 0:
        expires_at = (datetime.now(timezone.utc)
                      + timedelta(days=int(expires_days))).isoformat(timespec="seconds")
    entry = {
        "id": secrets.token_urlsafe(12),
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "kind": kind,
        "target_id": target_id,
        "title": (title or "").strip(),
        "allow_ask": bool(allow_ask),
        "expires_at": expires_at,
        "revoked": False,
        "view_count": 0,
        "last_viewed_at": "",
        "created_at": _now(),
        "note": note,
    }
    with _lock:
        rows = _all()
        rows.append(entry)
        _save_all(rows)
    return entry


def get_share(token: str) -> dict | None:
    """按 token 取分享（不看是否有效，调用方自行判断 live）。"""
    return next((s for s in _all() if s.get("id") == token), None)


def list_shares(workspace_id: str, owner_id: str) -> list[dict]:
    """本人的分享列表。空间管理员要审计全空间请走审计日志，不在这里放宽。"""
    rows = [s for s in _all()
            if s.get("workspace_id") == workspace_id and s.get("owner_id") == owner_id]
    return sorted(rows, key=lambda s: s.get("created_at", ""), reverse=True)


def revoke(token: str, *, workspace_id: str, owner_id: str) -> dict | None:
    """撤销。已撤销的再撤一次也算成功（幂等），但只有本人能撤。"""
    with _lock:
        rows = _all()
        entry = next((s for s in rows if s.get("id") == token), None)
        if entry is None:
            return None
        if entry.get("workspace_id") != workspace_id or entry.get("owner_id") != owner_id:
            return None
        entry["revoked"] = True
        entry["last_viewed_at"] = entry.get("last_viewed_at") or ""
        _save_all(rows)
    return entry


def touch_view(token: str) -> None:
    """记一次访问。匿名读不该因为写失败而 500，所以整体吞异常。"""
    try:
        with _lock:
            rows = _all()
            entry = next((s for s in rows if s.get("id") == token), None)
            if entry is None:
                return
            entry["view_count"] = int(entry.get("view_count") or 0) + 1
            entry["last_viewed_at"] = _now()
            _save_all(rows)
    except Exception as exc:  # noqa: BLE001
        logger.debug("访问计数失败（忽略）：%s", exc)


def payload(share: dict) -> dict:
    return {
        "id": share.get("id", ""),
        "kind": share.get("kind", ""),
        "target_id": share.get("target_id", ""),
        "title": share.get("title", ""),
        "allow_ask": bool(share.get("allow_ask")),
        "expires_at": share.get("expires_at", ""),
        "revoked": bool(share.get("revoked")),
        "live": is_live(share),
        "view_count": int(share.get("view_count") or 0),
        "last_viewed_at": share.get("last_viewed_at", ""),
        "created_at": share.get("created_at", ""),
        "note": share.get("note", ""),
        "owner_id": share.get("owner_id", ""),
        "workspace_id": share.get("workspace_id", "default"),
    }
