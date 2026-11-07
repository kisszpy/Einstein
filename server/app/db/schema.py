"""表结构与迁移。

规则：
1. 迁移只增不改——每次结构变化追加一条 `MIGRATIONS`，靠 `schema_version` 表记录已应用版本。
2. DDL 用同一份模板，`{{BYTES}}` 由方言展开（sqlite=BLOB / postgres=BYTEA），避免两套 SQL 漂移。
3. 所有实体主键为 TEXT（uuid hex），不用自增，方便跨库迁移与回放。
"""

from __future__ import annotations

from app.db.engine import Database

SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
)
"""

# 001：知识库域（KB / 文档 / 切片）+ 异步任务表
_MIGRATION_001 = [
    """
    CREATE TABLE IF NOT EXISTS knowledge_bases (
        id            TEXT PRIMARY KEY,
        workspace_id  TEXT NOT NULL DEFAULT 'default',
        name          TEXT NOT NULL,
        description   TEXT NOT NULL DEFAULT '',
        visibility    TEXT NOT NULL DEFAULT 'workspace',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_kb_ws ON knowledge_bases(workspace_id)",
    """
    CREATE TABLE IF NOT EXISTS documents (
        id            TEXT PRIMARY KEY,
        kb_id         TEXT NOT NULL,
        name          TEXT NOT NULL,
        content       TEXT NOT NULL DEFAULT '',
        content_hash  TEXT NOT NULL DEFAULT '',
        version       INTEGER NOT NULL DEFAULT 1,
        chunk_count   INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'ready',
        source        TEXT NOT NULL DEFAULT 'text',
        meta          TEXT NOT NULL DEFAULT '{}',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_kb ON documents(kb_id)",
    "CREATE INDEX IF NOT EXISTS idx_doc_hash ON documents(kb_id, content_hash)",
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id            TEXT PRIMARY KEY,
        kb_id         TEXT NOT NULL,
        document_id   TEXT NOT NULL,
        seq           INTEGER NOT NULL DEFAULT 0,
        text          TEXT NOT NULL,
        char_start    INTEGER NOT NULL DEFAULT 0,
        char_end      INTEGER NOT NULL DEFAULT 0,
        vector        {{BYTES}},
        vector_kind   TEXT NOT NULL DEFAULT 'mock',
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunk_kb ON chunks(kb_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(document_id)",
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id            TEXT PRIMARY KEY,
        kind          TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'pending',
        payload       TEXT NOT NULL DEFAULT '{}',
        result        TEXT NOT NULL DEFAULT '{}',
        attempts      INTEGER NOT NULL DEFAULT 0,
        max_attempts  INTEGER NOT NULL DEFAULT 3,
        error         TEXT NOT NULL DEFAULT '',
        workspace_id  TEXT NOT NULL DEFAULT 'default',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_status ON jobs(status, created_at)",
]

# 002：会话域（conversations / messages / agent_runs）
#
# messages 用 `seq` 而不是自增主键：自增语法两方言不一致（AUTOINCREMENT vs SERIAL），
# 而序号本来就由业务侧掌握（一次问答写入两条），显式 seq 反而更好排序与去重。
_MIGRATION_002 = [
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id            TEXT PRIMARY KEY,
        title         TEXT NOT NULL DEFAULT '',
        workspace_id  TEXT NOT NULL DEFAULT 'default',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_ws ON conversations(workspace_id, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS messages (
        id               TEXT PRIMARY KEY,
        conversation_id  TEXT NOT NULL,
        role             TEXT NOT NULL,
        content          TEXT NOT NULL DEFAULT '',
        seq              INTEGER NOT NULL DEFAULT 0,
        created_at       TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        id            TEXT PRIMARY KEY,
        session_id    TEXT NOT NULL DEFAULT '',
        turn          INTEGER NOT NULL DEFAULT 1,
        task          TEXT NOT NULL DEFAULT '',
        tool_ids      TEXT NOT NULL DEFAULT '[]',
        steps         TEXT NOT NULL DEFAULT '[]',
        answer        TEXT NOT NULL DEFAULT '',
        status        TEXT NOT NULL DEFAULT 'ok',
        workspace_id  TEXT NOT NULL DEFAULT 'default',
        project       TEXT NOT NULL DEFAULT '',
        artifacts     TEXT NOT NULL DEFAULT '[]',
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_ws ON agent_runs(workspace_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_run_session ON agent_runs(session_id, turn)",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, _MIGRATION_001),
    (2, _MIGRATION_002),
]

_TYPE_TOKENS = {
    "sqlite": {"{{BYTES}}": "BLOB"},
    "postgres": {"{{BYTES}}": "BYTEA"},
}


def _render(statements: list[str], dialect: str) -> list[str]:
    tokens = _TYPE_TOKENS[dialect]
    out = []
    for stmt in statements:
        for token, real in tokens.items():
            stmt = stmt.replace(token, real)
        out.append(stmt)
    return out


def current_version(db: Database) -> int:
    db.execute_ddl([SCHEMA_VERSION_TABLE])
    value = db.scalar("SELECT max(version) AS v FROM schema_version")
    return int(value or 0)


def migrate(db: Database, target: int | None = None) -> list[int]:
    """按序应用未执行的迁移，返回本次实际应用的版本号。"""
    applied: list[int] = []
    version = current_version(db)
    limit = target if target is not None else 10**6
    for number, statements in MIGRATIONS:
        if number <= version or number > limit:
            continue
        db.execute_ddl(_render(statements, db.dialect))
        db.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (number, _now()),
        )
        applied.append(number)
    return applied


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
