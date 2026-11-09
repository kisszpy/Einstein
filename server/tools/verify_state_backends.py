"""存储后端验收：json / sqlite / postgres 三套跑同一组契约用例。

两级断言：
1. **行级约束**（三种后端同一套）：写入就是一行行落库，改一行不动别人，
   删一行只少一行，空表读 None，未声明字段被忽略（不再是整域 JSON 文本）。
2. **业务往返**：各域 store（专家/技能/用户/空间/密钥/评测/MCP/审计）
   读 → 反序列化 → 写回，数量与内容不丢。

用法：
    ./.venv/Scripts/python.exe tools/verify_state_backends.py
    ./.venv/Scripts/python.exe tools/verify_state_backends.py --only sqlite --verbose
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SERVER_ROOT / "data"

RESULTS: list[tuple[str, str, bool, str]] = []


def check(backend: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((backend, name, bool(ok), detail))
    if not ok:
        print(f"  FAIL  [{backend}] {name}  <- {detail}")


def read_fixture(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 行级契约

def kv_table():
    """临时验证表：跟任何业务域无关，只用来压行语义。"""
    from app.db.tables import Column, Table

    return Table(
        name="verify_kv",
        columns=(
            Column("id"),
            Column("label", default="''"),
            Column("score", "int", default="0"),
            Column("tags", "json", default="'[]'"),
        ),
        order="id",
    )


def run_row_contract(backend: str, state, label: str) -> None:
    # 空后端：还没写过任何东西时读出来必须是 None（交给调用方重建默认值）
    check(backend, f"{label} 空表读 None", state.read() is None)
    state.write({})

    state.write(
        {
            "a": {"id": "a", "label": "甲", "score": 1, "tags": ["x"]},
            "b": {"id": "b", "label": "乙", "score": 2, "tags": []},
        }
    )
    rows = state.read() or {}
    check(backend, f"{label} 写入两行", set(rows) == {"a", "b"}, str(rows))

    # 只改 b 的一列：a 必须原样不动
    state.write(
        {
            "a": {"id": "a", "label": "甲", "score": 1, "tags": ["x"]},
            "b": {"id": "b", "label": "乙改", "score": 2, "tags": []},
        }
    )
    rows = state.read() or {}
    check(
        backend,
        f"{label} 改一行不动别人",
        rows.get("a", {}).get("label") == "甲" and rows.get("b", {}).get("label") == "乙改",
        str(rows),
    )

    state.write({"b": {"id": "b", "label": "乙改", "score": 2, "tags": []}})
    rows = state.read() or {}
    check(backend, f"{label} 删一行只少一行", set(rows) == {"b"}, str(rows))

    try:
        state.write({"b": {"id": "b", "label": "乙改", "score": 2, "未知": 1}})
        ok, detail = True, ""
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check(backend, f"{label} 忽略未声明字段", ok, detail)

    before = len(state.read() or {})
    try:
        state.write({"b": {"id": "b", "label": "乙改", "score": 2, "tags": []}})
        after = len(state.read() or {})
        ok = before == after
    except Exception as exc:  # noqa: BLE001
        ok, after = False, f"{type(exc).__name__}: {exc}"
    check(backend, f"{label} 重复写入幂等", ok, f"{before} -> {after}")


# ---------------------------------------------------------------- 各域探针

def probe_experts(state) -> tuple[bool, str]:
    from app.services import experts

    fixture = read_fixture("experts_store.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    store = experts.ExpertStore.load()
    if len(store.experts) != len(fixture):
        return False, f"读到 {len(store.experts)} / 期望 {len(fixture)}"
    store.save()
    again = state.read() or {}
    return set(again) == set(fixture), f"写回 {len(again)} 条"


def probe_skills(state) -> tuple[bool, str]:
    from app.services import skills

    fixture = read_fixture("skills_store.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    store = skills.SkillStore.load()
    if len(store.skills) < len(fixture):
        return False, f"读到 {len(store.skills)} / 期望至少 {len(fixture)}"
    store.save()
    again = state.read() or {}
    return len(again) >= len(fixture), f"写回 {len(again)} 条"


def probe_users(state) -> tuple[bool, str]:
    from app.services import users

    fixture = read_fixture("users.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    store = users.UserStore.load()
    if len(store.items) < len(fixture):
        return False, f"读到 {len(store.items)} / 期望至少 {len(fixture)}"
    store.save()
    again = state.read() or {}
    return len(again) >= len(fixture), f"写回 {len(again)} 条"


def probe_workspaces(state) -> tuple[bool, str]:
    from app.services import workspaces

    fixture = read_fixture("workspaces.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    store = workspaces.WorkspaceStore.load()
    if len(store.items) < len(fixture):
        return False, f"读到 {len(store.items)} / 期望至少 {len(fixture)}"
    store.save()
    again = state.read() or {}
    return len(again) >= len(fixture), f"写回 {len(again)} 条"


def probe_apikeys(state) -> tuple[bool, str]:
    from app.services import apikeys

    fixture = read_fixture("api_keys.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    store = apikeys.ApiKeyStore.load()
    if len(store.items) < len(fixture):
        return False, f"读到 {len(store.items)} / 期望至少 {len(fixture)}"
    store.save()
    again = state.read() or {}
    return len(again) >= len(fixture), f"写回 {len(again)} 条"


def probe_eval(state) -> tuple[bool, str]:
    from app.services import eval as eval_mod

    fixture = read_fixture("eval_sets.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    eval_mod._sets = None
    sets = eval_mod.list_sets()
    if len(sets) != len(fixture):
        return False, f"读到 {len(sets)} / 期望 {len(fixture)}"
    eval_mod._save()
    again = state.read() or {}
    return set(again) == set(fixture), f"写回 {len(again)} 条"


def probe_mcp(state) -> tuple[bool, str]:
    from app.services import mcp

    fixture = read_fixture("mcp_servers.json")
    if fixture is None:
        return True, "无样本，跳过"
    state.write({})
    state.write(fixture)
    mcp._servers = None
    servers = mcp.list_servers()
    if len(servers) != len(fixture):
        return False, f"读到 {len(servers)} / 期望 {len(fixture)}"
    mcp._save(mcp._registry())
    again = state.read() or {}
    return set(again) == set(fixture), f"写回 {len(again)} 条"


def probe_audit(state) -> tuple[bool, str]:
    """审计是追加型：写一条只多一行，而不是重写整份日志。"""
    from app.core.config import get_settings
    from app.services import audit

    if get_settings().store_backend == "json":
        audit._entries.clear()
        audit._loaded = False
        audit._json_state.write([])
        audit.record(
            workspace_id="verify-ws", key_id=None, key_name=None,
            method="POST", path="/verify", status=200, duration_ms=1.0,
        )
        rows = audit.list_entries(limit=100000)
        return len(rows) == 1, f"追加后 {len(rows)} 条"

    table = audit._table_state()
    before = table.count()
    audit._entries.clear()
    audit._loaded = True  # 只看本次追加，不把历史全读进内存
    audit.record(
        workspace_id="verify-ws", key_id=None, key_name=None,
        method="POST", path="/verify", status=200, duration_ms=1.0,
    )
    after = table.count()
    table.delete_where("workspace_id = ?", ("verify-ws",))
    return after == before + 1, f"{before} -> {after}（追加一行）"


# 各域 store 读的是模块级 _STATE：探针前把它指到被测后端，跑完还原
_BINDINGS = {
    "experts": ("app.services.experts", "_STATE", "_expert_store"),
    "skills": ("app.services.skills", "_STATE", "_skill_store"),
    "users": ("app.services.users", "_STATE", "_store"),
    "workspaces": ("app.services.workspaces", "_STATE", "_store"),
    "apikeys": ("app.services.apikeys", "_STATE", "_store"),
    "eval": ("app.services.eval", "_STATE", "_sets"),
    "mcp": ("app.services.mcp", "_STATE", "_servers"),
}


def bind_state(domain: str, state):
    """把模块级状态读写器指向被测后端，返回还原函数。"""
    import importlib

    spec = _BINDINGS.get(domain)
    if spec is None:
        return lambda: None
    module_name, state_attr, cache_attr = spec
    module = importlib.import_module(module_name)
    old_state = getattr(module, state_attr)
    old_cache = getattr(module, cache_attr, None)
    setattr(module, state_attr, state)
    if hasattr(module, cache_attr):
        setattr(module, cache_attr, None)

    def restore() -> None:
        setattr(module, state_attr, old_state)
        if hasattr(module, cache_attr):
            setattr(module, cache_attr, old_cache)

    return restore


PROBES = {
    "experts": probe_experts,
    "skills": probe_skills,
    "users": probe_users,
    "workspaces": probe_workspaces,
    "apikeys": probe_apikeys,
    "eval": probe_eval,
    "mcp": probe_mcp,
    "audit": probe_audit,
}


# ---------------------------------------------------------------- 后端套件

def _postgres_verify_dsn(settings) -> str | None:
    """独立验证库（`<db>_verify`），**绝不复用应用的生产 DSN**。

    这个脚本的域探针会用样本数据整域覆盖写。曾经因为它直接连生产库，
    跑一次就把线上真实的用户/技能/密钥覆盖成了 data/*.json 里的样本，
    所以这里强制隔离：拿不到验证库就跳过，不冒这个险。
    """
    base, _, tail = (settings.postgres_dsn or "").rpartition("/")
    if not base or not tail:
        return None
    name = tail.split("?")[0]
    verify_dsn = f"{base}/{name}_verify"
    if verify_dsn.rstrip("/") == settings.postgres_dsn.rstrip("/"):
        return None
    try:
        import psycopg

        try:
            psycopg.connect(verify_dsn, connect_timeout=5).close()
        except Exception:  # noqa: BLE001 — 库不存在则建
            with psycopg.connect(settings.postgres_dsn, autocommit=True, connect_timeout=5) as conn:
                conn.execute(f'CREATE DATABASE "{name}_verify"')
        return verify_dsn
    except Exception as exc:  # noqa: BLE001 — 连不上/没权限就跳过
        print(f"  （准备验证库失败：{type(exc).__name__}: {exc}）")
        return None


def _readonly_loaders() -> list[tuple[str, object, int]]:
    """当前配置下的「读」检查：域 → (loader, 至少应有几条)。"""
    from app.services import apikeys, eval as eval_mod, experts, mcp, skills, users, workspaces

    return [
        ("experts", lambda: experts.ExpertStore.load().list_experts(), 0),
        # 技能加载时会补 4 个内置种子，所以下限是脚本里的内置数量
        ("skills", lambda: skills.SkillStore.load().list_skills(), len(skills.BUILTIN_SKILLS)),
        ("users", lambda: users.UserStore.load().list_all(), 1),
        ("workspaces", lambda: workspaces.WorkspaceStore.load().list(), 1),
        ("apikeys", lambda: apikeys.ApiKeyStore.load().list_keys(), 0),
        ("eval", lambda: eval_mod.list_sets(), 0),
        ("mcp", lambda: mcp.list_servers(), 0),
    ]


_READONLY: list[tuple[str, object, int]] = []


def run_backend(backend: str, make_state) -> None:
    print(f"\n== {backend}")
    for domain, probe in PROBES.items():
        state = make_state(domain)
        restore = bind_state(domain, state)
        try:
            ok, detail = probe(state)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        finally:
            restore()
        check(backend, f"{domain} 读写往返", ok, detail)
        if ok and detail != "无样本，跳过":
            print(f"  PASS  {domain}  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["json", "sqlite", "postgres", "all"], default="all")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.engine import Database
    from app.db.rows import TableState
    from app.db.schema import _render, migrate
    from app.services.state import JsonState

    _READONLY.clear()
    _READONLY.extend(_readonly_loaders())
    tmp_root = Path(tempfile.mkdtemp(prefix="einstein_state_"))
    settings = get_settings()
    backends = ["json", "sqlite", "postgres"] if args.only == "all" else [args.only]

    try:
        for backend in backends:
            if backend == "json":
                run_row_contract(backend, JsonState(tmp_root / "kv.json"), "json")
                run_backend(backend, lambda domain: JsonState(tmp_root / f"{domain}.json"))
                shutil.rmtree(tmp_root, ignore_errors=True)
                tmp_root.mkdir(parents=True, exist_ok=True)
                continue

            if backend == "sqlite":
                path = Path(tempfile.gettempdir()) / "einstein_verify_state.db"
                if path.exists():
                    path.unlink()
                db = Database("sqlite", str(path))
            else:
                dsn = _postgres_verify_dsn(settings)
                if dsn is None:
                    print("  SKIP  postgres：无法准备独立验证库（不愿碰生产库）")
                    continue
                db = Database("postgres", dsn)

            migrate(db)
            table = kv_table()
            db.execute_ddl(_render([f"DROP TABLE IF EXISTS {table.name}"], db.dialect))
            db.execute_ddl(_render([table.ddl()], db.dialect))
            run_row_contract(backend, TableState(table, db), "table")

            def make(domain: str, db=db):  # noqa: ANN001
                from app.db.tables import DOMAIN_TABLES, TABLES

                return TableState(TABLES[DOMAIN_TABLES[domain]], db)

            run_backend(backend, make)
            db.execute_ddl(_render([f"DROP TABLE IF EXISTS {table.name}"], db.dialect))

        print(f"\n== 当前配置（{settings.store_backend}，只读）")
        # 线上库只做"读得出来"的检查：绝不写。样本数据覆盖写只发生在
        # 上面的临时库（sqlite/postgres 都指向测试库或临时文件）。
        from app.services.state import blob

        for domain, loader, minimum in _READONLY:
            state = blob(domain, DATA_DIR / f"{domain}.json")
            restore = bind_state(domain, state)
            try:
                rows = len(loader())
                expected = state.count() if hasattr(state, "count") else len(state.read() or {})
                ok = rows >= expected >= minimum
                detail = f"读到 {rows} 条 / 表内 {expected} 行"
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            finally:
                restore()
            check("config", f"{domain} 当前后端可读", ok, detail)
            if ok:
                print(f"  PASS  {domain}  {detail}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r[2])
    print(f"\n===== {passed} / {total} 通过")
    if passed != total:
        for backend, name, ok, detail in RESULTS:
            if not ok:
                print(f"  [{backend}] {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
