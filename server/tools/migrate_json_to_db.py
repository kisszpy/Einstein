"""把 data/kb_store.json 迁移到 SQLite / PostgreSQL。

特性：
- 幂等：按主键 INSERT ... DO NOTHING，可重复执行
- 只读源文件：绝不修改 kb_store.json，迁移后仍可用 json 后端跑
- --dry-run 先算账再动手
- 迁移完做数量核对，不一致直接报错退出

用法：
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py                # 用当前 store_backend
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py --dry-run
    ./.venv/Scripts/python.exe tools/migrate_json_to_db.py --backend sqlite --db data/einstein.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = SERVER_ROOT / "data" / "kb_store.json"


def load_source(path: Path) -> dict:
    if not path.exists():
        return {"kbs": {}, "documents": {}, "chunks": []}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None)
    parser.add_argument("--db", default=None, help="sqlite 文件路径（--backend sqlite 时生效）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.engine import Database
    from app.db.schema import migrate

    settings = get_settings()
    backend = args.backend or (settings.store_backend if settings.store_backend != "json" else "sqlite")
    dsn = (args.db or settings.sqlite_path) if backend == "sqlite" else settings.postgres_dsn

    payload = load_source(Path(args.source))
    kbs = payload.get("kbs") or {}
    documents = payload.get("documents") or {}
    chunks = payload.get("chunks") or []

    print(f"源文件：{args.source}")
    print(f"目标：{backend} · {dsn}")
    print(f"待迁移：{len(kbs)} 个知识库 / {len(documents)} 篇文档 / {len(chunks)} 个切片")
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

        from app.services.repositories.kb import vec_to_bytes

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

    # 核对
    got = {
        "kbs": db.scalar("SELECT count(*) FROM knowledge_bases"),
        "documents": db.scalar("SELECT count(*) FROM documents"),
        "chunks": db.scalar("SELECT count(*) FROM chunks"),
    }
    print(f"库内现有：{got['kbs']} / {got['documents']} / {got['chunks']}")
    if got["kbs"] < len(kbs) or got["documents"] < len(documents) or got["chunks"] < len(chunks):
        print("迁移不完整：库内数量少于源文件，请检查错误日志", file=sys.stderr)
        return 1
    print("迁移完成（幂等，可重复执行）")
    return 0


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
