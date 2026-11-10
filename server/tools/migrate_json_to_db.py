"""把 data/kb_store.json 与 data/history_store.json 迁移到 SQLite / PostgreSQL。

特性：
- 幂等：按主键 upsert，可重复执行
- 只读源文件：绝不修改源 JSON，迁移后仍可用 json 后端跑
- --dry-run 先算账再动手
- 迁移完做数量核对，不一致直接报错退出

用法：
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py                # 用当前 store_backend
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py --dry-run
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py --backend sqlite --db data/einstein.db
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py --only history
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = SERVER_ROOT / "data" / "kb_store.json"
DEFAULT_HISTORY = SERVER_ROOT / "data" / "history_store.json"
DEFAULT_DATA = SERVER_ROOT / "data"

# 配置域：整域一份 JSON，按域拆进各自的表（规范见 app/db/tables.py）
STATE_DOMAINS = {
    "experts": "experts_store.json",
    "skills": "skills_store.json",
    "users": "users.json",
    "workspaces": "workspaces.json",
    "workspace_members": "workspace_members.json",
    "workspace_invites": "workspace_invites.json",
    "sessions": "sessions.json",
    "apikeys": "api_keys.json",
    "audit": "audit.json",
    "eval": "eval_sets.json",
    "eval_runs": "eval_runs.json",
    "mcp": "mcp_servers.json",
    "feedback": "message_feedback.json",
    "memory": "workspace_memory.json",
}


def load_source(path: Path) -> dict:
    if not path.exists():
        return {"kbs": {}, "documents": {}, "chunks": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_history(path: Path) -> dict:
    if not path.exists():
        return {"conversations": {}, "agent_runs": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 源文件坏了要明确报出，而不是静默当空
        print(f"读取会话源文件失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return {"conversations": {}, "agent_runs": {}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--history-source", default=str(DEFAULT_HISTORY))
    parser.add_argument("--only", choices=["all", "kb", "history", "state"], default="all")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None)
    parser.add_argument("--db", default=None, help="sqlite 文件路径（--backend sqlite 时生效）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="库内已有不同内容时也覆盖")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.engine import Database
    from app.db.schema import migrate

    settings = get_settings()
    backend = args.backend or (settings.store_backend if settings.store_backend != "json" else "sqlite")
    dsn = (args.db or settings.sqlite_path) if backend == "sqlite" else settings.postgres_dsn

    do_kb = args.only in ("all", "kb")
    do_history = args.only in ("all", "history")
    do_state = args.only in ("all", "state")

    payload = load_source(Path(args.source)) if do_kb else {}
    kbs = payload.get("kbs") or {}
    documents = payload.get("documents") or {}
    chunks = payload.get("chunks") or []

    history_payload = load_history(Path(args.history_source)) if do_history else {}
    convs = history_payload.get("conversations") or {}
    runs = history_payload.get("agent_runs") or {}
    msg_count = sum(len(c.get("messages") or []) for c in convs.values())

    print(f"目标：{backend} · {dsn}")
    if do_kb:
        print(f"知识库源：{args.source}")
        print(f"  待迁移：{len(kbs)} 个知识库 / {len(documents)} 篇文档 / {len(chunks)} 个切片")
    if do_history:
        print(f"会话源：{args.history_source}")
        print(f"  待迁移：{len(convs)} 个会话 / {msg_count} 条消息 / {len(runs)} 条运行记录")
    if do_state:
        print(f"配置域源：{DEFAULT_DATA}")
        present = [d for d in STATE_DOMAINS if (DEFAULT_DATA / STATE_DOMAINS[d]).exists()]
        print(f"  待迁移：{len(present)} 个域（{'、'.join(present) or '无文件'}）")
    if args.dry_run:
        print("--dry-run：未写入任何数据")
        return 0

    db = Database(backend, dsn)
    applied = migrate(db)
    if applied:
        print(f"已应用迁移：{applied}")

    # 幂等插入：先试 IGNORE 冲突
    if backend == "sqlite":
        insert_ignore = "INSERT OR IGNORE"
        conflict = ""
    else:
        insert_ignore = "INSERT"
        conflict = " ON CONFLICT (id) DO NOTHING"

    now = _now()
    if do_kb:
        migrate_kb(db, kbs, documents, chunks, insert_ignore, conflict, now)
    if do_history:
        migrate_history(db, convs, runs, insert_ignore, conflict, now)
    if do_state:
        migrate_state(db, now, force=args.force)

    # 核对
    if do_kb:
        got = {
            "kbs": db.scalar("SELECT count(*) FROM knowledge_bases"),
            "documents": db.scalar("SELECT count(*) FROM documents"),
            "chunks": db.scalar("SELECT count(*) FROM chunks"),
        }
        print(f"库内知识库：{got['kbs']} / {got['documents']} / {got['chunks']}")
        if got["kbs"] < len(kbs) or got["documents"] < len(documents) or got["chunks"] < len(chunks):
            print("知识库迁移不完整：库内数量少于源文件，请检查错误日志", file=sys.stderr)
            return 1
    if do_history:
        got = {
            "conversations": db.scalar("SELECT count(*) FROM conversations"),
            "messages": db.scalar("SELECT count(*) FROM messages"),
            "agent_runs": db.scalar("SELECT count(*) FROM agent_runs"),
        }
        print(f"库内会话：{got['conversations']} / {got['messages']} / {got['agent_runs']}")
        if got["conversations"] < len(convs) or got["agent_runs"] < len(runs):
            print("会话迁移不完整：库内数量少于源文件，请检查错误日志", file=sys.stderr)
            return 1
    print("迁移完成（幂等，可重复执行）")
    return 0


def migrate_state(db, now: str, *, force: bool = False) -> int:
    """把配置域 JSON 按域拆进各自的表（一字段一列）。

    幂等：按主键 upsert，重复执行不会产生重复行。
    目标表里已有数据时默认跳过（避免用历史 JSON 覆盖线上改动），
    `--force` 才覆盖。
    """
    from app.db.legacy import import_json_file
    from app.db.rows import TableState
    from app.db.tables import DOMAIN_TABLES, TABLES

    moved = 0
    for domain, filename in STATE_DOMAINS.items():
        path = DEFAULT_DATA / filename
        if not path.exists():
            print(f"  {domain}: 跳过（{filename} 不存在）")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {domain}: 跳过（解析失败：{exc}）", file=sys.stderr)
            continue
        table_name = DOMAIN_TABLES.get(domain)
        if table_name is None:
            print(f"  {domain}: 跳过（没有对应表，请在 app/db/tables.py 登记）")
            continue
        count = len(payload) if isinstance(payload, (dict, list)) else 1
        existing = TableState(TABLES[table_name], db).count()
        if existing and not force:
            print(f"  {domain}: 表 {table_name} 已有 {existing} 行，跳过（--force 可覆盖）")
            continue
        written = import_json_file(domain, payload, db)
        print(f"  {domain} → {table_name}: 写入 {written} 行（源 {count} 条）")
        moved += 1
    return moved


def migrate_kb(db, kbs: dict, documents: dict, chunks: list, insert_ignore: str,
               conflict: str, now: str) -> None:
    from app.services.repositories.kb import vec_to_bytes

    with db.transaction():
        for kb_id, kb in kbs.items():
            db.execute(
                f"{insert_ignore} INTO knowledge_bases"
                " (id, workspace_id, name, description, visibility, created_at, updated_at)"
                f" VALUES (?, ?, ?, ?, ?, ?, ?){conflict}",
                (
                    kb_id, kb.get("workspace_id") or "default", kb.get("name") or "未命名",
                    kb.get("description") or "", kb.get("visibility") or "workspace",
                    kb.get("created_at") or now, kb.get("updated_at") or kb.get("created_at") or now,
                ),
            )
        for doc_id, doc in documents.items():
            db.execute(
                f"{insert_ignore} INTO documents"
                " (id, kb_id, name, content, content_hash, version, chunk_count, status, source, meta,"
                " created_at, updated_at)"
                f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?){conflict}",
                (
                    doc_id, doc.get("kb_id"), doc.get("name") or "未命名", doc.get("content") or "",
                    doc.get("content_hash") or "", int(doc.get("version") or 1),
                    int(doc.get("chunk_count") or 0), doc.get("status") or "ready",
                    doc.get("source") or "text",
                    json.dumps(doc.get("meta") or {}, ensure_ascii=False),
                    doc.get("created_at") or now, doc.get("updated_at") or doc.get("created_at") or now,
                ),
            )

        rows = []
        for idx, chunk in enumerate(chunks):
            vector = chunk.get("vector") or []
            rows.append(
                (
                    chunk.get("id") or f"{chunk.get('document_id')}-{idx}",
                    chunk.get("kb_id"), chunk.get("document_id"), int(chunk.get("seq") or idx),
                    chunk.get("text") or "", int(chunk.get("char_start") or 0),
                    int(chunk.get("char_end") or 0), vec_to_bytes(vector),
                    chunk.get("embedding_kind") or "mock", chunk.get("created_at") or now,
                )
            )
        db.executemany(
            f"{insert_ignore} INTO chunks"
            " (id, kb_id, document_id, seq, text, char_start, char_end, vector, vector_kind, created_at)"
            f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?){conflict}",
            rows,
        )


def migrate_history(db, convs: dict, runs: dict, insert_ignore: str,
                    conflict: str, now: str) -> None:
    """会话域迁移。

    消息 id 用 `{conversation_id}-{seq}` 生成稳定值：源文件里的消息没有 id，
    用序号拼出确定性主键才能保证重复执行不产生重复行。
    """
    with db.transaction():
        for conv_id, conv in convs.items():
            db.execute(
                f"{insert_ignore} INTO conversations"
                " (id, title, workspace_id, created_at, updated_at)"
                f" VALUES (?, ?, ?, ?, ?){conflict}",
                (
                    conv_id, conv.get("title") or "", conv.get("workspace_id") or "default",
                    conv.get("created_at") or now, conv.get("updated_at") or conv.get("created_at") or now,
                ),
            )
            rows = [
                (
                    f"{conv_id}-{seq}", conv_id, msg.get("role") or "user",
                    msg.get("content") or "", seq, msg.get("created_at") or now,
                )
                for seq, msg in enumerate(conv.get("messages") or [])
            ]
            if rows:
                db.executemany(
                    f"{insert_ignore} INTO messages"
                    " (id, conversation_id, role, content, seq, created_at)"
                    f" VALUES (?, ?, ?, ?, ?, ?){conflict}",
                    rows,
                )
        for run_id, run in runs.items():
            db.execute(
                f"{insert_ignore} INTO agent_runs"
                " (id, session_id, turn, task, tool_ids, steps, answer, status,"
                " workspace_id, project, artifacts, created_at)"
                f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?){conflict}",
                (
                    run_id, run.get("session_id") or "", int(run.get("turn") or 1),
                    run.get("task") or "",
                    json.dumps(run.get("tool_ids") or [], ensure_ascii=False),
                    json.dumps(run.get("steps") or [], ensure_ascii=False),
                    run.get("answer") or "", run.get("status") or "ok",
                    run.get("workspace_id") or "default", run.get("project") or "",
                    json.dumps(run.get("artifacts") or [], ensure_ascii=False),
                    run.get("created_at") or now,
                ),
            )



def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
