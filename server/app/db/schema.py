"""表结构与迁移。

规则：
1. 迁移只增不改——每次结构变化追加一条 `MIGRATIONS`，靠 `schema_version` 表记录已应用版本。
2. DDL 用同一份模板，`{{BYTES}}` 由方言展开（sqlite=BLOB / postgres=BYTEA），避免两套 SQL 漂移。
3. 所有实体主键为 TEXT（uuid hex），不用自增，方便跨库迁移与回放。
"""

from __future__ import annotations

from collections.abc import Callable

from app.db.engine import Database
from app.db.tables import PROJECTS as _PROJECTS_TABLE
from app.db.tables import ddl_all as _domain_tables_ddl

_PROJECTS_DDL = _PROJECTS_TABLE.ddl()

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

# 003：任务队列的租约与执行者
#
# 为什么要租约：worker 进程被 kill / 机器重启时，任务会永远卡在 running。
# 租约到期后由任意 worker 回收成 pending，最多重试 max_attempts 次。
_MIGRATION_003 = [
    "ALTER TABLE jobs ADD COLUMN lease_until TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN worker_id TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_job_lease ON jobs(status, lease_until)",
]

_MIGRATION_004 = [
    """
    CREATE TABLE IF NOT EXISTS domain_state (
        domain     TEXT PRIMARY KEY,
        payload    TEXT NOT NULL,
        version    INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )
    """,
]

_MIGRATION_005 = [
    "ALTER TABLE jobs ADD COLUMN progress TEXT NOT NULL DEFAULT '{}'",
    """
    CREATE TABLE IF NOT EXISTS document_versions (
        id            TEXT PRIMARY KEY,
        document_id   TEXT NOT NULL,
        kb_id         TEXT NOT NULL,
        version       INTEGER NOT NULL,
        content       TEXT NOT NULL DEFAULT '',
        content_hash  TEXT NOT NULL DEFAULT '',
        chunk_count   INTEGER NOT NULL DEFAULT 0,
        source        TEXT NOT NULL DEFAULT 'text',
        note          TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ver_doc ON document_versions(document_id, version)",
]

# 006：S2.3 检索质量 —— 父子切片（chunks.parent_seq）+ 评测历史（eval_runs）
_MIGRATION_006 = [
    "ALTER TABLE chunks ADD COLUMN parent_seq INTEGER",
    """
    CREATE TABLE IF NOT EXISTS eval_runs (
        id     TEXT PRIMARY KEY,
        set_id TEXT NOT NULL,
        kb_id  TEXT NOT NULL,
        mode   TEXT NOT NULL DEFAULT 'default',
        report TEXT NOT NULL,
        ran_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_runs_set ON eval_runs(set_id, ran_at)",
]

# 007：S2.5 对话体验 —— 会话归档/置顶
_MIGRATION_007 = [
    "ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
]

# 008：拆掉 domain_state —— 每个配置域一张表、一个字段一列
#
# 原先 11 个域（用户/工作区/成员/专家/技能/MCP/API Key/评测/审计/反馈/记忆）
# 共用 `domain_state(domain, payload)`，一域一行、整域一个 JSON 文本：
# 数据库看不见字段、无法按行更新、两个实例改不同用户也互相覆盖、审计每记
# 一条日志要重写整域。这里按 `app/db/tables.py` 的规范建真表；
# 存量数据由 `app/db/legacy.py` 从 domain_state 拆进新表（幂等）。
_MIGRATION_008 = _domain_tables_ddl()

# 009：资源归属 owner_id —— 用户级隔离
#
# 原先所有资源只按 workspace 过滤，同一空间的两个用户互相全见。现在：
# - 配置类资源（专家/技能/MCP/知识库）owner_id='' 表示系统预置、全员可见，
#   其余为创建者 user_id，只有本人可见（管理员也不越权，与个人空间边界一致）；
# - 运行类数据（会话/智能体运行）一律有 owner_id，只有创建者本人可见。
#
# 为什么用可调用对象而不是静态 SQL：`ddl_all()`（迁移 008）在新库上已经带着
# owner_id 建表，静态 ALTER 会在新库重复加列报错。这里按「列是否存在」决定，
# 老库补列、新库只建索引，两种库都幂等。
_OWNER_TABLES = (
    "conversations",
    "agent_runs",
    "knowledge_bases",
    "experts",
    "skills",
    "mcp_servers",
    "api_keys",
)

_OWNER_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner_id, workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_run_owner ON agent_runs(owner_id, workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_kb_owner ON knowledge_bases(owner_id, workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_experts_owner ON experts(owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_owner ON mcp_servers(owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_apikeys_owner ON api_keys(owner_id)",
]


def _column_exists(db: Database, table: str, column: str) -> bool:
    if db.dialect == "postgres":
        row = db.query_one(
            "SELECT count(*) AS n FROM information_schema.columns"
            " WHERE table_name = ? AND column_name = ?",
            (table, column),
        )
        return bool(row and int(row["n"] or 0))
    return any(r.get("name") == column for r in db.query(f"PRAGMA table_info({table})"))


def _migration_009(db: Database) -> list[str]:
    statements = [
        f"ALTER TABLE {table} ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''"
        for table in _OWNER_TABLES
        if not _column_exists(db, table, "owner_id")
    ]
    return statements + _OWNER_INDEXES


# 迁移条目可以是静态语句列表，也可以是 (db) -> 语句列表 的函数（按现有结构决定 DDL）
Migration = list[str] | Callable[[Database], list[str]]

# 010：项目登记表 —— 「目录即项目」改为「登记过的才是项目」
#
# 工作区根目录既是用户项目的家，也是运行时目录的家（技能同步目录、临时计算
# 沙盒）。早先按目录列举项目，结果 `skills/` 被当成项目出现在界面上，而且
# 项目没有归属，同空间的成员互相看得见。现在项目先登记再创建：
# - 只有 projects 表里存在、且 owner_id = 当前用户的行才会列出；
# - internal=1 的运行时目录（_scratch）永不展示；
# - 存量目录由启动时的 projects.adopt_orphans() 收养（归空间 owner）。
#
# 新库由迁移 008 的 ddl_all() 一并建表，这里用 IF NOT EXISTS 保持幂等。
_MIGRATION_010 = [
    _PROJECTS_DDL,
    "CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(workspace_id, owner_id)",
]

MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _MIGRATION_001),
    (2, _MIGRATION_002),
    (3, _MIGRATION_003),
    (4, _MIGRATION_004),
    (5, _MIGRATION_005),
    (6, _MIGRATION_006),
    (7, _MIGRATION_007),
    (8, _MIGRATION_008),
    (9, _migration_009),
    (10, _MIGRATION_010),
]

_TYPE_TOKENS = {
    "sqlite": {
        "{{BYTES}}": "BLOB",
        "{{BOOL}}": "INTEGER",
        "{{REAL}}": "REAL",
        "{{TRUE}}": "1",
        "{{FALSE}}": "0",
    },
    # 布尔用原生 BOOLEAN（psycopg 直接接受 Python bool）；
    # 浮点用 DOUBLE PRECISION，避免 REAL(float4) 把 0.7 存成 0.699999988
    "postgres": {
        "{{BYTES}}": "BYTEA",
        "{{BOOL}}": "BOOLEAN",
        "{{REAL}}": "DOUBLE PRECISION",
        "{{TRUE}}": "TRUE",
        "{{FALSE}}": "FALSE",
    },
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
        ddl = statements(db) if callable(statements) else statements
        db.execute_ddl(_render(ddl, db.dialect))
        db.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (number, _now()),
        )
        applied.append(number)
    return applied


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
