"""从 domain_state / JSON 文件把存量配置域拆进各自的表。

为什么需要这一步：`domain_state(domain, payload)` 里现在还躺着线上数据
（用户、专家、技能、审计…），拆表不能让用户数据消失。这里按域逐个搬：

- 幂等：每域搬完在 `legacy_imports` 里留一条书签，之后即使目标表被清空
  也不会被历史数据"复活"；
- 只搬认识的域（`tables.DOMAIN_TABLES`），不认识的留在 domain_state 里并告警；
- 各域 payload 形状不同（字典/数组/按工作区分组/按评测集分组），由
  `_rows_of` 逐个展开成行。

**这里的 print 是有意保留的**：本模块由 `tools/migrate_legacy.py` 这样的
命令行入口调用，`verbose=True` 时逐域打印进度是给人看的交互输出，不是服务
日志——改走 logger 反而会被级别过滤掉，运维敲完命令看不到任何反馈。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.engine import Database
from app.db.rows import TableState
from app.db.tables import DOMAIN_TABLES, LEGACY_IMPORTS, TABLES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rows_of(domain: str, payload: Any) -> list[dict]:
    """把各种历史形状展开成行列表。"""
    if domain == "audit":
        # 老的审计条目没有 id，补一个；行数上限由 audit 模块自己裁剪
        return [{**entry, "id": entry.get("id") or uuid.uuid4().hex} for entry in (payload or [])]

    if domain == "feedback":
        return [dict(e) for e in (payload or []) if isinstance(e, dict)]

    if domain == "memory":
        # {workspace_id: [条目…]}
        rows: list[dict] = []
        for ws_id, items in (payload or {}).items():
            for entry in items or []:
                rows.append({**entry, "workspace_id": entry.get("workspace_id") or ws_id})
        return rows

    if domain == "eval_runs":
        # {set_id: [报告…]}，报告里自带 run_id 的很少，缺就补
        rows = []
        for set_id, reports in (payload or {}).items():
            for report in reports or []:
                rows.append(
                    {
                        "id": report.get("run_id") or uuid.uuid4().hex,
                        "set_id": report.get("set_id") or set_id,
                        "kb_id": report.get("kb_id", ""),
                        "mode": report.get("mode") or "default",
                        "report": report,
                        "ran_at": report.get("ran_at") or _now(),
                    }
                )
        return rows

    if domain == "workspace_invites":
        # 老 JSON 里的字段叫 ws_id，表里按职责叫 workspace_id
        rows = []
        for code, value in (payload or {}).items():
            invite = dict(value or {})
            invite.setdefault("code", code)
            if "workspace_id" not in invite:
                invite["workspace_id"] = invite.pop("ws_id", "")
            rows.append(invite)
        return rows

    if isinstance(payload, dict):
        rows = []
        for key, value in payload.items():
            row = dict(value) if isinstance(value, dict) else {}
            table = TABLES[DOMAIN_TABLES[domain]]
            if len(table.key) == 1:
                row.setdefault(table.key[0], key)
            elif table.key_sep:
                parts = str(key).split(table.key_sep)
                if len(parts) == len(table.key):
                    for name, part in zip(table.key, parts):
                        row.setdefault(name, part)
            rows.append(row)
        return rows

    return []


def import_domain_state(db: Database, *, verbose: bool = True) -> dict[str, int]:
    """把 domain_state 里各域搬进新表，返回 {domain: 导入行数}。"""
    try:
        exist = db.query_one(
            "SELECT count(*) AS n FROM information_schema.tables"
            " WHERE table_name = 'domain_state'"
        ) if db.dialect == "postgres" else db.query_one(
            "SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name='domain_state'"
        )
    except Exception:  # noqa: BLE001 — 探测失败就当没有历史表
        exist = None
    if not exist or not int(exist.get("n") or 0):
        return {}

    done = {row["domain"] for row in TableState(LEGACY_IMPORTS, db).rows()}
    imported: dict[str, int] = {}
    payloads: dict[str, Any] = {}
    for record in db.query("SELECT domain, payload FROM domain_state"):
        domain = record["domain"]
        try:
            payload = json.loads(record["payload"])
        except (TypeError, ValueError):
            continue
        payloads[domain] = payload
        if domain in done:
            continue
        table_name = DOMAIN_TABLES.get(domain)
        if table_name is None:
            if verbose:
                print(f"[legacy] 未知域 {domain}，保留在 domain_state 不迁移", flush=True)
            continue
        rows = _rows_of(domain, payload)
        state = TableState(TABLES[table_name], db)
        added = 0
        if rows and state.count() == 0:
            state.insert_many(rows)
            added = len(rows)
        TableState(LEGACY_IMPORTS, db).upsert(
            {"domain": domain, "imported_at": _now(), "rows": added}
        )
        imported[domain] = added
        if verbose:
            print(f"[legacy] {domain} → {table_name}: 写入 {added} 行", flush=True)

    _backfill_memberships(payloads, db, verbose=verbose)
    return imported


def _backfill_memberships(payloads: dict[str, Any], db: Database, *, verbose: bool) -> None:
    """老 users 表里的 workspaces 列表 → 成员关系（拆分归属职责的配套一步）。

    users 表从此不存归属，`user.workspaces` 只是成员表的一个投影。存量账号
    原来所在的共享空间如果没进成员表，这里补上，避免升级后全员看不见数据。
    """
    members = TableState(TABLES["workspace_members"], db)
    if members.count() > 0:
        return
    legacy_users = payloads.get("users") or {}
    rows: list[dict] = []
    for user_id, user in legacy_users.items():
        if not isinstance(user, dict):
            continue
        role = "admin" if user.get("role") in ("owner", "admin") else (user.get("role") or "member")
        for ws_id in user.get("workspaces") or []:
            rows.append(
                {
                    "user_id": user.get("id") or user_id,
                    "workspace_id": ws_id,
                    "role": "owner" if ws_id == "default" and user.get("role") == "owner" else role,
                    "is_personal": False,
                    "joined_at": _now(),
                }
            )
    if rows:
        members.insert_many(rows)
        if verbose:
            print(f"[legacy] users.workspaces → workspace_members: {len(rows)} 条", flush=True)


def import_json_file(domain: str, payload: Any, db: Database) -> int:
    """把某个域的 JSON 文件内容搬进表（migrate_json_to_db 用），返回行数。"""
    table_name = DOMAIN_TABLES.get(domain)
    if table_name is None:
        return 0
    rows = _rows_of(domain, payload)
    if not rows:
        return 0
    table = TABLES[table_name]
    state = TableState(table, db)
    for row in rows:
        state.upsert(row)
    return len(rows)


# 拆表前还留在 JSON 文件里、且"丢了会让用户直接有感"的两个小域：
# 会话（否则所有人被登出）、邀请码（否则发出去的码失效）。
_FILE_DOMAINS = {"sessions": "sessions.json", "workspace_invites": "workspace_invites.json"}


def import_json_backed(db: Database, data_dir, *, verbose: bool = True) -> dict[str, int]:  # noqa: ANN001
    """把会话与邀请码的 JSON 文件搬进表（仅当表为空时执行，搬过就留书签）。"""
    from pathlib import Path

    imported: dict[str, int] = {}
    bookmarks = TableState(LEGACY_IMPORTS, db)
    done = {row["domain"] for row in bookmarks.rows()}
    for domain, filename in _FILE_DOMAINS.items():
        marker = f"file:{domain}"
        if marker in done:
            continue
        table_name = DOMAIN_TABLES[domain]
        state = TableState(TABLES[table_name], db)
        if state.count() > 0:
            bookmarks.upsert({"domain": marker, "imported_at": _now(), "rows": 0})
            continue
        path = Path(data_dir) / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (TypeError, ValueError):
            continue
        rows = _rows_of(domain, payload)
        if rows:
            state.insert_many(rows)
            imported[domain] = len(rows)
            if verbose:
                print(f"[legacy] {filename} → {table_name}: {len(rows)} 行", flush=True)
        bookmarks.upsert({"domain": marker, "imported_at": _now(), "rows": len(rows)})
    return imported
