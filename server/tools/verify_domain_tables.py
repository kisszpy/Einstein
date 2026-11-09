"""配置域拆表验收：`domain_state` 不再是业务存储。

背景：早期 11 个配置域共用一张 `domain_state(domain, payload)`，一域一行、
整域一个 JSON。这个脚本检查拆表之后必须成立的六件事：

1. 每个域都有独立的表，且列是**类型化的字段**（不是只有一个 payload 列）；
2. 关键职责没有混在一张表里：`users` 不存归属（归属在 workspace_members），
   `audit_logs` 一条日志一行；
3. 写入是行级的：改一行只影响那一行；
4. 存量 `domain_state` 数据能被幂等搬进新表（含 users.workspaces → 成员关系）；
5. `blob(domain)` 返回的是 TableState，不再有任何 SqlState / payload 写入；
6. 域注册表与表规范一致（没有登记了却建不出的域）。

用法：
    ./.venv/Scripts/python.exe tools/verify_domain_tables.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]

PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"PASS  {name}" + (f"  （{detail}）" if detail else ""))
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"FAIL  {name}  <- {detail}")


def main() -> int:
    from app.core.config import get_settings
    from app.db.engine import Database
    from app.db.rows import TableState
    from app.db.schema import migrate
    from app.db.tables import DOMAIN_TABLES, TABLES

    settings = get_settings()
    print(f"当前后端：{settings.store_backend}")

    # 用临时 sqlite 做结构验收，不碰线上库
    tmp = Path(tempfile.mkdtemp(prefix="einstein_tables_")) / "verify.db"
    db = Database("sqlite", str(tmp))
    migrate(db)

    # 1. 每个域一张表，列数 > 1 且没有 payload 列
    for domain, table_name in DOMAIN_TABLES.items():
        columns = [row["name"] for row in db.query(f"PRAGMA table_info({table_name})")]
        ok = bool(columns) and "payload" not in columns and len(columns) > 1
        check(f"{domain} → {table_name} 是类型化表", ok, f"{len(columns)} 列")

    # 2. 职责边界
    user_cols = [r["name"] for r in db.query("PRAGMA table_info(users)")]
    check(
        "users 表不存归属（归属在 workspace_members）",
        "workspaces" not in user_cols and "workspace_ids" not in user_cols,
        ",".join(user_cols),
    )
    audit_cols = [r["name"] for r in db.query("PRAGMA table_info(audit_logs)")]
    check(
        "audit_logs 一条日志一行（有 status/path/ts 等独立列）",
        {"ts", "status", "path", "method", "workspace_id"} <= set(audit_cols),
        f"{len(audit_cols)} 列",
    )
    member_cols = [r["name"] for r in db.query("PRAGMA table_info(workspace_members)")]
    check(
        "workspace_members 复合主键 (user_id, workspace_id)",
        {"user_id", "workspace_id", "role", "is_personal"} <= set(member_cols),
        ",".join(member_cols),
    )

    # 3. 行级写：改一行不动别人
    experts = TableState(TABLES["experts"], db)
    experts.write(
        {
            "e1": {"id": "e1", "name": "甲", "workspace_id": "w1", "kb_ids": ["k1"], "temperature": 0.7},
            "e2": {"id": "e2", "name": "乙", "workspace_id": "w1", "kb_ids": [], "temperature": 0.3},
        }
    )
    experts.write(
        {
            "e1": {"id": "e1", "name": "甲", "workspace_id": "w1", "kb_ids": ["k1"], "temperature": 0.7},
            "e2": {"id": "e2", "name": "乙改", "workspace_id": "w1", "kb_ids": [], "temperature": 0.3},
        }
    )
    rows = {r["id"]: r for r in experts.rows()}
    check(
        "改一行只影响该行（甲未被重写）",
        rows["e1"]["name"] == "甲" and rows["e2"]["name"] == "乙改",
        f"{rows['e1']['name']}/{rows['e2']['name']}",
    )
    check("json 列往返（kb_ids）", rows["e1"]["kb_ids"] == ["k1"], str(rows["e1"]["kb_ids"]))
    check("real 列往返（temperature）", abs(rows["e2"]["temperature"] - 0.3) < 1e-9,
          str(rows["e2"]["temperature"]))

    experts.write({"e1": {"id": "e1", "name": "甲", "workspace_id": "w1"}})
    check("删一行只少一行", [r["id"] for r in experts.rows()] == ["e1"],
          str([r["id"] for r in experts.rows()]))

    # 4. 存量拆表：domain_state → 各表
    db.execute(
        "INSERT INTO domain_state (domain, payload, version, updated_at) VALUES (?, ?, ?, ?)",
        (
            "users",
            json.dumps(
                {
                    "u1": {
                        "id": "u1", "username": "admin", "display_name": "管理员", "role": "owner",
                        "password_hash": "h", "salt": "s", "workspaces": ["default"],
                        "created_at": "2026-01-01", "disabled": False,
                    }
                },
                ensure_ascii=False,
            ),
            1,
            "2026-11-01",
        ),
    )
    db.execute(
        "INSERT INTO domain_state (domain, payload, version, updated_at) VALUES (?, ?, ?, ?)",
        (
            "memory",
            json.dumps({"default": [{"id": "m1", "content": "事实", "source": "manual"}]}, ensure_ascii=False),
            1,
            "2026-11-01",
        ),
    )
    from app.db.legacy import import_domain_state

    first = import_domain_state(db, verbose=False)
    second = import_domain_state(db, verbose=False)
    check("存量拆表导入", first.get("users") == 1 and first.get("memory") == 1, str(first))
    check("拆表导入幂等（第二次为空）", second == {}, str(second))
    check(
        "users.workspaces 落成成员关系",
        db.query_one("SELECT role FROM workspace_members WHERE user_id = 'u1'") is not None,
    )
    check(
        "按工作区分组的记忆展开成行",
        db.query_one("SELECT workspace_id FROM workspace_memory WHERE id = 'm1'")["workspace_id"]
        == "default",
    )

    # 5. blob() 在库后端返回 TableState；未知域直接报错（不允许再回落成整域 JSON）
    if settings.store_backend != "json":
        from app.services.state import blob

        state = blob("experts", SERVER_ROOT / "data" / "experts_store.json")
        check("blob(experts) 返回 TableState", isinstance(state, TableState), type(state).__name__)
        try:
            blob("not_registered_domain", SERVER_ROOT / "data" / "x.json")
            ok, detail = False, "未报错"
        except KeyError as exc:
            ok, detail = True, str(exc)
        check("未登记域名直接报错（不再退回整域列）", ok, detail)

    # 6. 注册表自洽：DOMAIN_TABLES 的表都在 TABLES 里
    missing = [t for t in DOMAIN_TABLES.values() if t not in TABLES]
    check("域 → 表注册表自洽", not missing, str(missing))

    print(f"\n===== {PASSED} 通过 / {PASSED + len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print(f"  {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
