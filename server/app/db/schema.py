"""表结构与迁移。

规则：
1. 迁移只增不改——每次结构变化追加一条 `MIGRATIONS`，靠 `schema_version` 表记录已应用版本。
2. DDL 用同一份模板，`{{BYTES}}` 由方言展开（sqlite=BLOB / postgres=BYTEA），避免两套 SQL 漂移。
3. 所有实体主键为 TEXT（uuid hex），不用自增，方便跨库迁移与回放。
"""

from __future__ import annotations

from collections.abc import Callable

from app.db.engine import Database
from app.db.tables import (
    AGENT_APPROVALS as _AGENT_APPROVALS_TABLE,
    PROJECTS as _PROJECTS_TABLE,
    ddl_all as _domain_tables_ddl,
)

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
        visibility    TEXT NOT NULL DEFAULT 'private',
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
    """,    "CREATE INDEX IF NOT EXISTS idx_chunk_kb ON chunks(kb_id)",
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

# 011：人工确认记录 —— 审批从「进程内 set」改为带归属与时效的行
#
# 旧实现是模块级 `_APPROVALS: set[(session_id, tool)]`：多 worker 不共享、
# 重启即清、不校验 session 归属（可用别人的 id 预批准）、批准一次永久放行。
# 现在每条审批是一行：谁（owner_id）在哪个会话为准哪个工具（+参数指纹）
# 做了决定，什么时候过期。批准与 thread 的 checkpoint 配对使用。
_MIGRATION_011 = [
    _AGENT_APPROVALS_TABLE.ddl(),
    "CREATE INDEX IF NOT EXISTS idx_approvals_sess "
    "ON agent_approvals(workspace_id, owner_id, session_id)",
]

# 012：切片记嵌入指纹 —— 「向量是谁产的」要能查
#
# 以前 chunk 只记 vector_kind（remote/mock），不记模型名与维度。换了嵌入模型
# （8B→4B，4096→2560 维）之后：Milvus 写入报"维度冲突"但文案不指向根因；
# 内存后端更糟——静默丢弃维度不符的切片，检索永远返回空，hybrid 只剩 BM25，
# 没人会发现。记下 model + dim，检索前就能给出"请重建索引"这种可操作的提示。
def _migration_012(db: Database) -> list[str]:
    statements = []
    if not _column_exists(db, "chunks", "embedding_model"):
        statements.append(
            "ALTER TABLE chunks ADD COLUMN embedding_model TEXT NOT NULL DEFAULT ''"
        )
    if not _column_exists(db, "chunks", "embedding_dim"):
        statements.append(
            "ALTER TABLE chunks ADD COLUMN embedding_dim INTEGER NOT NULL DEFAULT 0"
        )
    return statements


# 013：可见性语义收紧。
#
# v0.7.0 之前 list_kbs 按 owner 过滤，visible='workspace' 只是占位、并不真的
# 让同事看见。现在放开 list 后，存量里所有 'workspace' 的库会一夜之间对全空间
# 可见——这等于把"私有"悄悄改成"公开"。所以迁移时把存量统一落成 private，
# 共享必须由用户显式选择。
def _migration_013(db: Database) -> list[str]:
    return [
        "UPDATE knowledge_bases SET visibility = 'private' WHERE visibility = 'workspace'",
    ]


# 014：按 run 的运行轨迹。
#
# 以前只有 metrics 的计数（请求耗时、每次 LLM 调用），没有因果链：
# 一次智能体答错，既不知道检索召回了什么，也不知道哪个工具慢了。
def _migration_014(db: Database) -> list[str]:
    from app.db.tables import TABLES

    return [
        TABLES["run_traces"].ddl(),
        "CREATE INDEX IF NOT EXISTS idx_run_traces_ws ON run_traces(workspace_id, started_at)",
    ]


# 015：模型注册表 + 专家可指定模型。
#
# 模型信息原先只活在 `.env`（一个 base_url + 一个 model + 一个 key），换模型
# 只能改配置文件重启，页面上也看不到当前用的是哪个模型。这张表把模型变成
# 可管理资源，并给 experts 加 model_id —— 写作类专家要长上下文、代码类专家
# 要强推理，一个全局模型只能折中。
#
# 不预置任何行：没有 `is_default=1` 的行时全站继续用 `.env`，老部署升级后
# 行为不变；用户显式建模型并设为默认，才切换到注册表。
def _migration_015(db: Database) -> list[str]:
    from app.db.tables import TABLES

    statements = [
        TABLES["models"].ddl(),
        "CREATE INDEX IF NOT EXISTS idx_models_ws ON models(workspace_id, owner_id)",
    ]
    if not _column_exists(db, "experts", "model_id"):
        statements.append("ALTER TABLE experts ADD COLUMN model_id TEXT NOT NULL DEFAULT ''")
    # 013 之后专家/技能的三档可见性只存在于内存对象里——表里没有这列，
    # 写库时被静默丢弃，重启后全部退回 private。这里把列真正建出来。
    for table in ("experts", "skills"):
        if not _column_exists(db, table, "visibility"):
            statements.append(
                f"ALTER TABLE {table} ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'"
            )
    return statements


# 016：成本看板要的几列（015 之后才想清楚）。
#
# 为什么要单开一条而不是改 015：015 已经在一些库上应用过了，`migrate` 按
# 版本号跳过——改动已应用的迁移，老库永远拿不到新列，而且没有任何提示。
# 迁移只增不改，是这个表的唯一纪律。
def _migration_016(db: Database) -> list[str]:
    statements: list[str] = []
    # 单价：把 token 折算成钱。0 = 未填，报表只显示用量不显示金额。
    for column in ("price_in", "price_out"):
        if not _column_exists(db, "models", column):
            statements.append(f"ALTER TABLE models ADD COLUMN {column} REAL NOT NULL DEFAULT 0")
    # 花费快照：按当时单价定格，改价不回头改写历史账
    if not _column_exists(db, "run_traces", "cost"):
        statements.append("ALTER TABLE run_traces ADD COLUMN cost REAL NOT NULL DEFAULT 0")
    # 成员用量视图要显示人名。按 id 反查用户表在报表里是 N 次查询；
    # 用户名改一次的代价远小于每次翻报表都 join。
    if not _column_exists(db, "run_traces", "owner_name"):
        statements.append(
            "ALTER TABLE run_traces ADD COLUMN owner_name TEXT NOT NULL DEFAULT ''"
        )
    return statements


# 017：告警规则与事件。
#
# 没有告警的运维是事后考古：值班的人只能等用户来投诉。规则覆盖最常出事的四类——
# 错误率、未命中率、每日花费、模型不可用。渠道先做 webhook（企业微信/钉钉/通用
# JSON 都吃这个口），不引入邮件服务依赖。
def _migration_017(db: Database) -> list[str]:
    from app.db.tables import TABLES

    return [
        TABLES["alert_rules"].ddl(),
        TABLES["alert_events"].ddl(),
        "CREATE INDEX IF NOT EXISTS idx_alert_rules_ws ON alert_rules(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_alert_events_ws ON alert_events(workspace_id, fired_at)",
    ]


# 018：知识库级授权（"这个库只给财务组"）。
#
# 三档可见性只能表达"给全空间看"或"给所有人看"，表达不了"只开给某几个人"。
# 授权是叠加层：不改变可见性语义，只在它之上额外放行几个人（read/write 两档）。
def _migration_018(db: Database) -> list[str]:
    from app.db.tables import TABLES

    return [
        TABLES["kb_grants"].ddl(),
        "CREATE INDEX IF NOT EXISTS idx_kb_grants_user ON kb_grants(user_id)",
    ]


# 019：任务模板。
#
# "每周竞品简报"这类活每次重述一遍任务，措辞一变模型选的路线也跟着变。
# 模板只固化起步参数（任务描述、挂载、专家），执行仍由智能体自主规划。
def _migration_019(db: Database) -> list[str]:
    from app.db.tables import TABLES

    return [
        TABLES["task_templates"].ddl(),
        "CREATE INDEX IF NOT EXISTS idx_templates_ws ON task_templates(workspace_id)",
    ]


# 020：发布与嵌入（免登录只读链接）。
#
# 分享单独成表而不是给 conversations 加一列：同一份内容可以有多条分享
# （不同有效期），撤销要能追溯，访问计数与"允许追问"这些字段也会独立演化。
def _migration_020(db: Database) -> list[str]:
    from app.db.tables import TABLES

    return [
        TABLES["shares"].ddl(),
        "CREATE INDEX IF NOT EXISTS idx_shares_owner ON shares(workspace_id, owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_shares_target ON shares(target_id)",
    ]


def _migration_021(db: Database) -> list[str]:
    from app.db.tables import TABLES

    return [
        TABLES["run_events"].ddl(),
        # 重放与跟读都按 (run_id, seq) 顺序扫，这条索引决定重连是不是秒开
        "CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq)",
        # 「我还有哪次运行没看完」按空间+人查
        "CREATE INDEX IF NOT EXISTS idx_run_events_owner ON run_events(workspace_id, owner_id)",
    ]


MIGRATIONS: list[tuple[int, Migration]] = [    (1, _MIGRATION_001),
    (2, _MIGRATION_002),
    (3, _MIGRATION_003),
    (4, _MIGRATION_004),
    (5, _MIGRATION_005),
    (6, _MIGRATION_006),
    (7, _MIGRATION_007),
    (8, _MIGRATION_008),
    (9, _migration_009),
    (10, _MIGRATION_010),
    (11, _MIGRATION_011),
    (12, _migration_012),
    (13, _migration_013),
    (14, _migration_014),
    (15, _migration_015),
    (16, _migration_016),
    (17, _migration_017),
    (18, _migration_018),
    (19, _migration_019),
    (20, _migration_020),
    (21, _migration_021),
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
