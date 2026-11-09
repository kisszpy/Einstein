"""配置域的表规范：每张表有自己的列，一个字段一列。

背景：早期所有配置域（用户、专家、技能、API Key、审计…）共用一张
`domain_state(domain, payload)`，一域一行、整域一个 JSON 文本。问题在于：

- 数据库看不见任何字段，改一列要读整域再写整域，无法按行更新，也无法建索引；
- 两个实例同时改同一域时互相覆盖整份数据（哪怕改的是不同用户）；
- 审计这种高频追加的域，每记一条日志就要重写上百 KB 的整域 JSON。

现在每个域一张表、每个字段一列；只有真正嵌套的结构（技能的文件清单、
专家的技能 id 列表）才用 JSON 列——那是「文档内的数组」，不是「整域塞一列」。

表规范用代码声明（单一事实来源），DDL 由 `ddl()` 生成：
- `schema.py` 的迁移直接展开这些 DDL，避免「代码里一套、迁移里另一套」漂移；
- `rows.py` 的读写按同一份规范做类型转换（bool / json / real）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Column", "Table", "TABLES", "DOMAIN_TABLES", "table_of", "ddl_all"]

# 方言差异只在类型上：bool / real 用占位符，由 schema._render 展开
_TYPE_SQL = {
    "text": "TEXT",
    "int": "INTEGER",
    "real": "{{REAL}}",
    "bool": "{{BOOL}}",
    "json": "TEXT",
}


@dataclass(frozen=True)
class Column:
    """一列 = 一个字段。

    name  ：数据库列名（避开 SQL 关键字，如 commit→source_commit）
    field ：dataclass / dict 里的字段名，默认与列名相同
    kind  ：text | int | real | bool | json
    """

    name: str
    kind: str = "text"
    field: str | None = None
    nullable: bool = False
    default: str | None = None

    @property
    def attr(self) -> str:
        return self.field or self.name


@dataclass(frozen=True)
class Table:
    """一张表：列 + 主键 + 稳定读出顺序。

    key      ：主键列（支持复合主键，如 workspace_members 的 (user_id, workspace_id)）
    key_sep  ：复合主键在「字典视角」下的拼接符（成员表沿用 "user@ws" 的历史键）
    """

    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...] = ("id",)
    key_sep: str = ""
    order: str = ""

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"{self.name} 没有列 {name}")

    def ddl(self) -> str:
        parts: list[str] = []
        for col in self.columns:
            piece = f"{col.name} {_TYPE_SQL[col.kind]}"
            if not col.nullable:
                piece += " NOT NULL"
            if col.default is not None:
                default = col.default
                if col.kind == "bool":
                    # Postgres 的布尔列不接受 DEFAULT 0/1，用占位符按方言展开
                    default = {"0": "{{FALSE}}", "1": "{{TRUE}}"}.get(default, default)
                piece += f" DEFAULT {default}"
            if len(self.key) == 1 and col.name == self.key[0]:
                piece += " PRIMARY KEY"
            parts.append(piece)
        if len(self.key) > 1:
            parts.append(f"PRIMARY KEY ({', '.join(self.key)})")
        body = ",\n        ".join(parts)
        return f"CREATE TABLE IF NOT EXISTS {self.name} (\n        {body}\n    )"


def _c(name: str, kind: str = "text", **kw) -> Column:
    return Column(name=name, kind=kind, **kw)


# ---------------------------------------------------------------- 账号与租户

USERS = Table(
    name="users",
    # 注意：没有 workspaces 列。账号归属（谁在哪个空间、什么角色）是
    # workspace_members 表的职责；这里只放账号本身的身份与凭据。
    columns=(
        _c("id"),
        _c("username"),
        _c("display_name", default="''"),
        _c("role", default="'member'"),
        _c("password_hash", default="''"),
        _c("salt", default="''"),
        _c("created_at", default="''"),
        _c("last_login_at", nullable=True),
        _c("disabled", "bool", default="0"),
    ),
    order="created_at",
)

WORKSPACES = Table(
    name="workspaces",
    columns=(
        _c("id"),
        _c("name"),
        _c("description", default="''"),
        _c("created_at", default="''"),
    ),
    order="created_at",
)

WORKSPACE_MEMBERS = Table(
    name="workspace_members",
    columns=(
        _c("user_id"),
        _c("workspace_id"),
        _c("role", default="'member'"),
        _c("is_personal", "bool", default="0"),
        _c("joined_at", default="''"),
    ),
    key=("user_id", "workspace_id"),
    key_sep="@",
    order="joined_at",
)

WORKSPACE_INVITES = Table(
    name="workspace_invites",
    columns=(
        _c("code"),
        _c("workspace_id"),
        _c("role", default="'member'"),
        _c("max_uses", "int", default="0"),
        _c("uses", "int", default="0"),
        _c("created_at", default="''"),
    ),
    key=("code",),
    order="created_at",
)

SESSIONS = Table(
    name="user_sessions",
    columns=(
        _c("token_hash"),
        _c("user_id"),
        _c("user_agent", default="''"),
        _c("created_at", default="''"),
        _c("expires_at", default="''"),
    ),
    key=("token_hash",),
    order="created_at",
)

# ---------------------------------------------------------------- 智能体配置

# 项目（用户可见的「工作台产物目录」）。
#
# 为什么项目要有自己的表：早先「目录即项目」——`data/projects/<ws>/` 下有什么
# 目录，界面就列什么。可这个根目录同时住着运行时目录（技能、临时沙盒），
# 于是技能目录被当成项目列出来，用户在「文件」里能看到 `skills/`；而且项目
# 没有归属，同一空间的两个用户互相看得见对方的项目。
#
# 现在项目是先登记再创建的资源：只有本表里存在且属于当前用户的行才会出现在
# 列表里，磁盘目录只是它的落点。`internal=1` 的是运行时目录（如 _scratch），
# 永远不对外展示。
PROJECTS = Table(
    name="projects",
    columns=(
        _c("workspace_id", default="'default'"),
        _c("slug"),
        # 创建者 user_id。与专家/技能不同，项目没有「系统预置」这一档：
        # 每一个项目都归属于某个人，空间管理员也看不到别人的项目。
        _c("owner_id", default="''"),
        _c("name", default="''"),
        _c("description", default="''"),
        _c("template", default="'blank'"),
        _c("internal", "bool", default="0"),
        _c("created_at", default="''"),
        _c("updated_at", default="''"),
    ),
    key=("workspace_id", "slug"),
    key_sep="@",
    order="updated_at DESC",
)

EXPERTS = Table(
    name="experts",
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        # '' = 预置专家（全员可见）；其余为创建者 user_id（仅本人可见）
        _c("owner_id", default="''"),
        _c("name"),
        _c("description", default="''"),
        _c("emoji", default="'🤖'"),
        _c("persona", default="''"),
        _c("greeting", default="''"),
        _c("status", default="'draft'"),
        _c("published_at", default="''"),
        _c("temperature", "real", default="0.7"),
        _c("enabled", "bool", default="1"),
        _c("skill_ids", "json", default="'[]'"),
        _c("kb_ids", "json", default="'[]'"),
        _c("tool_ids", "json", default="'[]'"),
        _c("suggestions", "json", default="'[]'"),
    ),
    order="name",
)

SKILLS = Table(
    name="skills",
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        # '' = 预置技能（内置/GitHub 预装，全员可见）；其余为安装者 user_id
        _c("owner_id", default="''"),
        _c("name"),
        _c("description", default="''"),
        _c("system_prompt", default="''"),
        _c("builtin", "bool", default="0"),
        _c("enabled", "bool", default="1"),
        _c("source", default="'local'"),
        _c("source_url", default="''"),
        _c("repo", default="''"),
        _c("source_ref", default="''", field="ref"),
        _c("source_commit", default="''", field="commit"),
        _c("subpath", default="''"),
        _c("author", default="''"),
        _c("license", default="''"),
        _c("installed_at", default="''"),
        _c("updated_at", default="''"),
        _c("version", default="'0.1.0'"),
        _c("tools", "json", default="'[]'"),
        _c("files", "json", default="'[]'"),
        _c("versions", "json", default="'[]'"),
    ),
    order="id",
)

MCP_SERVERS = Table(
    name="mcp_servers",
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        # '' = 平台预置连接（全员可见）；其余为添加者 user_id（仅本人可见）
        _c("owner_id", default="''"),
        _c("name"),
        _c("transport", default="'stdio'"),
        _c("command", default="''"),
        _c("args", "json", default="'[]'"),
        _c("env", "json", default="'{}'"),
        _c("url", default="''"),
        _c("enabled", "bool", default="1"),
        _c("approved_tools", "json", default="'[]'"),
        _c("status", default="'unknown'"),
        _c("tools", "json", default="'[]'"),
        _c("last_error", default="''"),
        _c("refreshed_at", default="''"),
    ),
    order="name",
)

API_KEYS = Table(
    name="api_keys",
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        # 签发者：密钥继承签发者的资源可见范围（防止一把 key 读遍整个空间）
        _c("owner_id", default="''"),
        _c("name"),
        _c("key_hash"),
        _c("prefix", default="''"),
        _c("scopes", "json", default="'[]'"),
        _c("created_at", default="''"),
        _c("last_used_at", nullable=True),
        _c("revoked", "bool", default="0"),
        _c("call_count", "int", default="0"),
    ),
    order="created_at",
)

# ---------------------------------------------------------------- 质量与观测

EVAL_SETS = Table(
    name="eval_sets",
    columns=(
        _c("id"),
        _c("kb_id"),
        _c("name"),
        _c("items", "json", default="'[]'"),
        _c("created_at", default="''"),
    ),
    order="created_at",
)

# 迁移 006 已建过同名表（结构一致），这里声明它是为了让读写与 DDL 共用一份规范
EVAL_RUNS = Table(
    name="eval_runs",
    columns=(
        _c("id"),
        _c("set_id"),
        _c("kb_id", default="''"),
        _c("mode", default="''"),
        _c("report", "json", default="'{}'"),
        _c("ran_at", default="''"),
    ),
    order="ran_at",
)

AUDIT_LOGS = Table(
    name="audit_logs",
    columns=(
        _c("id"),
        _c("ts"),
        _c("workspace_id", default="'-'"),
        _c("key_id", nullable=True),
        _c("key_name", nullable=True),
        _c("method", default="''"),
        _c("path", default="''"),
        _c("status", "int", default="0"),
        _c("duration_ms", "real", default="0"),
        _c("tokens_in", "int", default="0"),
        _c("tokens_out", "int", default="0"),
        _c("detail", default="''"),
    ),
    order="ts",
)

MESSAGE_FEEDBACK = Table(
    name="message_feedback",
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        _c("session_id", default="''"),
        _c("conversation_id", default="''"),
        _c("turn", "int", default="1"),
        _c("rating"),
        _c("reason", default="''"),
        _c("user_name", default="''", field="user"),
        _c("created_at", default="''"),
    ),
    order="created_at",
)

WORKSPACE_MEMORY = Table(
    name="workspace_memory",
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        _c("content"),
        _c("source", default="'manual'"),
        _c("created_at", default="''"),
    ),
    order="created_at",
)

# 迁移书签：记录某个域已从 domain_state 拆表完毕，避免目标表清空后被历史数据复活
LEGACY_IMPORTS = Table(
    name="legacy_imports",
    columns=(
        _c("domain"),
        _c("imported_at", default="''"),
        _c("rows", "int", default="0"),
    ),
    key=("domain",),
    order="domain",
)

AGENT_APPROVALS = Table(
    name="agent_approvals",
    # 人工确认不是「进程里一个 set」：那样撑不过重启、没有归属校验，还能
    # 用别人的 session_id 预批准。这里按 (会话, 工具, 参数指纹) 落一条记录，
    # 带 TTL 与归属，批准只对这一次确认有效。
    columns=(
        _c("id"),
        _c("workspace_id", default="'default'"),
        _c("owner_id", default="''"),
        _c("session_id"),
        _c("tool"),
        # 参数指纹：工具相同但参数变了（比如 code 换了一段）要重新确认
        _c("args_hash", default="''"),
        _c("decision", default="'approve'"),
        _c("consumed", "bool", default="0"),
        _c("created_at", default="''"),
        _c("expires_at", default="''"),
    ),
    order="created_at DESC",
)

TABLES: dict[str, Table] = {
    t.name: t
    for t in (
        USERS,
        WORKSPACES,
        WORKSPACE_MEMBERS,
        WORKSPACE_INVITES,
        SESSIONS,
        PROJECTS,
        EXPERTS,
        SKILLS,
        MCP_SERVERS,
        API_KEYS,
        EVAL_SETS,
        EVAL_RUNS,
        AUDIT_LOGS,
        MESSAGE_FEEDBACK,
        WORKSPACE_MEMORY,
        LEGACY_IMPORTS,
        AGENT_APPROVALS,
    )
}

# 历史 blob 域名 → 表名（domain_state 里的 domain 取值）
DOMAIN_TABLES: dict[str, str] = {
    "apikeys": "api_keys",
    "audit": "audit_logs",
    "eval": "eval_sets",
    "eval_runs": "eval_runs",
    "experts": "experts",
    "feedback": "message_feedback",
    "mcp": "mcp_servers",
    "memory": "workspace_memory",
    "sessions": "user_sessions",
    "skills": "skills",
    "users": "users",
    "workspace_members": "workspace_members",
    "workspace_invites": "workspace_invites",
    "workspaces": "workspaces",
}


def table_of(domain: str) -> Table | None:
    name = DOMAIN_TABLES.get(domain)
    return TABLES[name] if name else None


def ddl_all() -> list[str]:
    """全部建表语句（含必建索引），由迁移 008 展开执行。"""
    statements = [t.ddl() for t in TABLES.values()]
    statements += [
        "CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(workspace_id, owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_experts_ws ON experts(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_skills_ws ON skills(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_apikeys_ws ON api_keys(workspace_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_mcp_ws ON mcp_servers(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_members_ws ON workspace_members(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_invites_ws ON workspace_invites(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_ws ON audit_logs(workspace_id, ts)",
        "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts)",
        "CREATE INDEX IF NOT EXISTS idx_feedback_ws ON message_feedback(workspace_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_memory_ws ON workspace_memory(workspace_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username ON users(username)",
    ]
    return statements
