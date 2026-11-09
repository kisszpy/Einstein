"""人工确认记录：归属 + 时效 + 参数指纹。

**为什么不再用进程内 set**：旧实现是 `set[(session_id, tool)]`，四个问题
一起存在——多 worker 不共享、重启即清、`/approve` 不校验会话归属（拿到别人的
session_id 就能预批准）、批准一次对该会话的该工具永久放行。审批是安全边界，
不能靠"反正用户会再点一次"来糊。

现在每条审批是一行：谁（`owner_id`）在哪个工作区、哪个会话，为准哪个工具、
哪组参数做了决定，什么时候过期。约定：

- **归属**：读写都带 `workspace_id + owner_id`，别人的记录查不到（不区分
  "不存在"与"不是你的"，避免越权探测）。
- **时效**：默认 30 分钟（`APP_AGENT_APPROVAL_TTL_MINUTES`）。过期即视为未
  批准——宁可多问一次，不可永久放行。
- **粒度**：`(session_id, tool, args_hash)`。参数变了（同一工具换了代码）
  要重新确认；这是"逐次确认"的近似实现，又不至于每一步都打断用户。
- **一次性**：批准被续跑消费后置 `consumed=1`，同一条记录不会被反复使用。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db.engine import Database, get_db
from app.db.schema import migrate

__all__ = [
    "args_fingerprint",
    "consume",
    "decide",
    "granted_tools",
    "latest_decision",
    "purge_expired",
    "request",
]

_ready = False


def _db() -> Database:
    """首次调用时确保表存在（迁移幂等，与其它 store 同一套路）。"""
    global _ready
    db = get_db()
    if not _ready:
        migrate(db)
        _ready = True
    return db


def args_fingerprint(args: object) -> str:
    """工具参数指纹：同样的调用只确认一次，改了参数就重新确认。"""
    try:
        blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(args)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insert(
    workspace_id: str, owner_id: str, session_id: str, tool: str,
    *, args_hash: str, decision: str,
) -> dict:
    ttl = max(1, int(get_settings().agent_approval_ttl_minutes))
    created = _now()
    row = {
        "id": uuid.uuid4().hex,
        "workspace_id": workspace_id or "default",
        "owner_id": owner_id,
        "session_id": session_id,
        "tool": tool,
        "args_hash": args_hash,
        "decision": decision,
        "consumed": False,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(minutes=ttl)).isoformat(),
    }
    _db().execute(
        "INSERT INTO agent_approvals "
        "(id, workspace_id, owner_id, session_id, tool, args_hash, decision, consumed, "
        " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"], row["workspace_id"], row["owner_id"], row["session_id"],
            row["tool"], row["args_hash"], row["decision"],
            # Postgres 的 BOOLEAN 列不接受 1/0（sqlite 接受），统一传 bool
            bool(row["consumed"]), row["created_at"], row["expires_at"],
        ),
    )
    return row


def request(
    workspace_id: str, owner_id: str, session_id: str, tool: str, *, args_hash: str = "",
) -> dict:
    """记一条「等待确认」：任务暂停时写入，/approve 凭它定位到底在批什么。

    只带 (workspace, owner) 查询——别人的 session 在这里查不到任何记录，
    于是 /approve 对越权请求只能回 404。
    """
    return _insert(workspace_id, owner_id, session_id, tool,
                   args_hash=args_hash, decision="pending")


def decide(
    workspace_id: str, owner_id: str, session_id: str, *,
    decision: str = "approve", args_hash: str = "",
) -> dict | None:
    """把最近一条待确认记录改成 approve / reject，返回该记录。

    `args_hash` 给了就只匹配参数一致的那条：参数变了说明不是同一个调用，
    应该重新弹确认框而不是沿用旧决定。
    """
    if not session_id:
        return None
    row = _latest(workspace_id, owner_id, session_id, decision="pending", args_hash=args_hash)
    if row is None:
        return None
    verdict = decision if decision in ("approve", "reject") else "approve"
    _db().execute("UPDATE agent_approvals SET decision = ? WHERE id = ?", (verdict, row["id"]))
    row["decision"] = verdict
    return row


def _latest(
    workspace_id: str, owner_id: str, session_id: str,
    *, decision: str | None = None, args_hash: str = "",
) -> dict | None:
    sql = (
        "SELECT * FROM agent_approvals "
        "WHERE workspace_id = ? AND owner_id = ? AND session_id = ? AND expires_at > ?"
    )
    params: list = [workspace_id or "default", owner_id, session_id, _now().isoformat()]
    if decision is not None:
        sql += " AND decision = ?"
        params.append(decision)
    if args_hash:
        sql += " AND args_hash = ?"
        params.append(args_hash)
    sql += " ORDER BY created_at DESC"
    rows = _db().query(sql, tuple(params))
    return dict(rows[0]) if rows else None


def latest_decision(workspace_id: str, owner_id: str, session_id: str) -> dict | None:
    """最近一次已作出的决定（approve/reject），未消费且未过期。续跑时用它。

    必须排除 `consumed`：一次批准只够续跑一次。否则同一会话里第二次遇到
    同一个敏感工具时，会翻出上一条已用过的批准直接放行——又变回"批一次
    永久放行"，而这正是这张表要解决的问题。
    """
    row = _latest(workspace_id, owner_id, session_id)
    if row is None or row["decision"] not in ("approve", "reject"):
        return None
    if row.get("consumed"):
        return None
    return row


def granted_tools(workspace_id: str, owner_id: str, session_id: str) -> set[str]:
    """本会话当前有效的「已批准工具」集合。

    用于兜底路径：检查点不可用（进程重启过）时按批准重新跑一遍，至少不会
    再弹一次确认框。同样只看未消费的记录——兜底不等于永久豁免。
    """
    if not session_id:
        return set()
    rows = _db().query(
        "SELECT DISTINCT tool FROM agent_approvals "
        "WHERE workspace_id = ? AND owner_id = ? AND session_id = ? "
        "AND decision = 'approve' AND consumed = ? AND expires_at > ?",
        (workspace_id or "default", owner_id, session_id, False, _now().isoformat()),
    )
    return {r["tool"] for r in rows}


def consume(record_id: str) -> None:
    _db().execute("UPDATE agent_approvals SET consumed = ? WHERE id = ?", (True, record_id))


def purge_expired() -> int:
    """删过期记录：审批是短期状态，留久了只会变成越权面。"""
    db = _db()
    now = _now().isoformat()
    removed = int(db.scalar("SELECT count(*) AS n FROM agent_approvals WHERE expires_at <= ?",
                            (now,)) or 0)
    if removed:
        db.execute("DELETE FROM agent_approvals WHERE expires_at <= ?", (now,))
    return removed
