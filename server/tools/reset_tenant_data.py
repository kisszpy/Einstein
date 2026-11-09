"""清空租户数据：只留管理员账号 + 预置技能/专家。

清什么：会话与消息、智能体运行记录、知识库/文档/分块/版本、API 密钥、
审计、评测、反馈、记忆、邀请码、登录会话、测试用 MCP、其它账号与空间、
项目工作目录与上传文件、Milvus 向量集合。

留什么：
- 账号：username=admin（及其个人空间、default 空间的 owner 成员关系）；
- 技能：builtin=1 的内置技能 + source=github 的已安装技能，
  以及被系统/专家实际引用的本地技能（slidep 版式、提示词类）；
- 专家：全部保留，但清掉 kb_ids（知识库已清空）；
- 结构：schema_version、legacy_imports（迁移书签，防止历史数据复活）。

用法：
    python tools/reset_tenant_data.py --dry-run   # 只打印将删除的行数
    python tools/reset_tenant_data.py --yes       # 真正执行
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SERVER_ROOT / "data"

# 整表清空（结构保留）
CLEAR_TABLES = [
    "agent_runs",
    "messages",
    "conversations",
    "chunks",
    "document_versions",
    "documents",
    "knowledge_bases",
    "projects",
    "api_keys",
    "audit_logs",
    "eval_sets",
    "eval_runs",
    "jobs",
    "mcp_servers",
    "message_feedback",
    "workspace_memory",
    "workspace_invites",
    "user_sessions",
]

# 需要保留的本地技能：被代码钉装（slidep 版式）或专家挂载（提示词）
KEEP_LOCAL_SKILLS = {"5dad1aa9277f", "158e56ee3c8b", "cffe71eb6139"}

# 测试遗留、必须删掉的本地技能
DROP_SKILLS = {"36a189d6a42c"}  # 「验证-端到端」


def _count(db, table: str) -> int:
    rows = db.query(f"SELECT COUNT(*) AS n FROM {table}")
    return int(rows[0]["n"]) if rows else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="确认执行（否则只做 dry-run）")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.engine import Database

    settings = get_settings()
    if settings.store_backend != "postgres":
        print(f"当前后端是 {settings.store_backend}，本脚本只针对 postgres 线上库")
        return 2
    db = Database("postgres", settings.postgres_dsn)

    admin = db.query("SELECT id, username FROM users WHERE username = 'admin'")
    if not admin:
        print("找不到 admin 账号，中止")
        return 2
    admin_id = admin[0]["id"]
    personal_id = f"p-{admin_id}"
    keep_ws = ["default", personal_id]

    print(f"管理员：{admin_id}（个人空间 {personal_id}）")
    print("待清空：")
    for table in CLEAR_TABLES:
        print(f"  {table:<20} {_count(db, table):>6} 行")

    users = db.query("SELECT id, username FROM users WHERE id <> ?", (admin_id,))
    spaces = db.query(
        "SELECT id, name FROM workspaces WHERE id <> ? AND id <> ?", ("default", personal_id)
    )
    print(f"  users 删除 {len(users)} 个：{[u['username'] for u in users]}")
    print(f"  workspaces 删除 {len(spaces)} 个：{[w['id'] for w in spaces]}")
    print(f"  skills 删除测试技能：{sorted(DROP_SKILLS)}")

    if not args.yes:
        print("\ndry-run 结束，确认后加 --yes 执行")
        return 0

    # ---- 1. 整表清空 ----
    for table in CLEAR_TABLES:
        db.execute(f"DELETE FROM {table}")

    # ---- 2. 技能：删测试技能，其余收敛到 default ----
    for skill_id in DROP_SKILLS:
        db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        shutil.rmtree(DATA_DIR / "skills" / skill_id, ignore_errors=True)
    db.execute("UPDATE skills SET workspace_id = 'default'")

    # ---- 3. 专家：全部保留，清空知识库挂载 ----
    db.execute("UPDATE experts SET kb_ids = '[]'")

    # ---- 4. 账号与空间 ----
    placeholders = ", ".join(["?"] * len(keep_ws))
    # 先按人删（default 空间里还有别人的成员关系），再按空间删
    db.execute("DELETE FROM workspace_members WHERE user_id <> ?", (admin_id,))
    db.execute(
        f"DELETE FROM workspace_members WHERE workspace_id NOT IN ({placeholders})", tuple(keep_ws)
    )
    db.execute("DELETE FROM users WHERE id <> ?", (admin_id,))
    db.execute(
        f"DELETE FROM workspaces WHERE id NOT IN ({placeholders})", tuple(keep_ws)
    )
    # 管理员必须是 default 的 owner、个人空间的 owner
    db.execute(
        "UPDATE workspace_members SET role = 'owner' WHERE user_id = ? AND workspace_id = 'default'",
        (admin_id,),
    )
    if not db.query(
        "SELECT 1 FROM workspace_members WHERE user_id = ? AND workspace_id = ?",
        (admin_id, personal_id),
    ):
        db.execute(
            "INSERT INTO workspace_members (user_id, workspace_id, role, is_personal, joined_at)"
            " VALUES (?, ?, 'owner', TRUE, '')",
            (admin_id, personal_id),
        )

    # ---- 5. 旧 blob 表：结构一并删掉（备份在 backups/ 的 pg_dump 里）----
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_name = 'domain_state'"):
        db.execute_ddl(["DROP TABLE IF EXISTS domain_state"])

    # ---- 6. 文件层：项目目录、上传、预览缓存 ----
    projects = DATA_DIR / "projects"
    if projects.is_dir():
        for child in projects.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    uploads = DATA_DIR / "uploads"
    if uploads.is_dir():
        for child in uploads.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    for cache in (".slidep", ".deckpreview"):
        shutil.rmtree(SERVER_ROOT / cache, ignore_errors=True)

    print("\n完成。剩余：")
    for table in ("users", "workspaces", "workspace_members", "experts", "skills",
                  "knowledge_bases", "documents", "conversations", "api_keys", "audit_logs"):
        print(f"  {table:<20} {_count(db, table):>6} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
