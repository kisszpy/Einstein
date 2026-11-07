"""配置域状态后端验收：json / sqlite / postgres 三套跑同一组用例。

覆盖 8 个域（专家、技能、用户、工作区、API Key、审计、评测集、MCP），
每个域验证「从状态读出 → 业务对象 → 再写回」，确保换后端后业务层无感。

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


# ---------------------------------------------------------------- 各域探针


def probe_experts(state) -> tuple[bool, str]:
    from app.services import experts

    fixture = read_fixture("experts_store.json")
    if fixture is None:
        return True, "无样本，跳过"
    experts._STATE = state
    experts._expert_store = None
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
    skills._STATE = state
    skills._skill_store = None
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
    users._STATE = state
    users._store = None
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
    workspaces._STATE = state
    workspaces._store = None
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
    apikeys._STATE = state
    apikeys._store = None
    state.write(fixture)
    store = apikeys.ApiKeyStore.load()
    if len(store.items) < len(fixture):
        return False, f"读到 {len(store.items)} / 期望至少 {len(fixture)}"
    store.save()
    again = state.read() or {}
    return len(again) >= len(fixture), f"写回 {len(again)} 条"


def probe_audit(state) -> tuple[bool, str]:
    from app.services import audit

    fixture = read_fixture("audit.json")
    if fixture is None:
        return True, "无样本，跳过"
    audit._STATE = state
    audit._entries.clear()
    audit._loaded = False
    state.write(fixture[-audit.MAX_ENTRIES :])
    rows = audit.list_entries(limit=100000)
    if len(rows) != min(len(fixture), audit.MAX_ENTRIES):
        return False, f"读到 {len(rows)} / 期望 {min(len(fixture), audit.MAX_ENTRIES)}"
    audit.record(
        workspace_id="verify-ws",
        key_id=None,
        key_name=None,
        method="POST",
        path="/verify",
        status=200,
        duration_ms=1.0,
    )
    again = state.read() or []
    return isinstance(again, list) and len(again) == len(rows) + 1, f"追加后 {len(again)} 条"


def probe_eval(state) -> tuple[bool, str]:
    from app.services import eval as eval_mod

    fixture = read_fixture("eval_sets.json")
    if fixture is None:
        return True, "无样本，跳过"
    eval_mod._STATE = state
    eval_mod._sets = None
    state.write(fixture)
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
    mcp._STATE = state
    mcp._servers = None
    state.write(fixture)
    servers = mcp.list_servers()
    if len(servers) != len(fixture):
        return False, f"读到 {len(servers)} / 期望 {len(fixture)}"
    mcp._save(mcp._registry())
    again = state.read() or {}
    return set(again) == set(fixture), f"写回 {len(again)} 条"


PROBES = {
    "experts": probe_experts,
    "skills": probe_skills,
    "users": probe_users,
    "workspaces": probe_workspaces,
    "apikeys": probe_apikeys,
    "audit": probe_audit,
    "eval": probe_eval,
    "mcp": probe_mcp,
}


# ---------------------------------------------------------------- 后端套件


def run_backend(backend: str, make_state, cleanup) -> None:
    from app.services.state import StateConflict

    print(f"\n== {backend}")
    for domain, probe in PROBES.items():
        state = make_state(domain)
        try:
            ok, detail = probe(state)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(backend, f"{domain} 读写往返", ok, detail)
        if ok:
            print(f"  PASS  {domain}")

    # 空域：读出 None 不报错
    empty = make_state("verify_missing")
    check(backend, "空域返回 None", empty.read() is None)

    # 覆盖写：版本递增
    target = make_state("verify_version")
    target.write({"a": 1})
    target.write({"a": 2})
    check(backend, "覆盖写保留最后一次", (target.read() or {}).get("a") == 2)

    # 并发写：多线程同时写同一个域，最终必须是完整 JSON（不撕裂）
    import threading

    def hammer(i: int) -> None:
        try:
            make_state("verify_concurrent").write({"who": i, "pad": "x" * 2000})
        except StateConflict:
            pass

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = make_state("verify_concurrent").read()
    ok = isinstance(final, dict) and isinstance(final.get("who"), int) and len(final.get("pad", "")) == 2000
    check(backend, "并发写不丢结构", ok, str(final)[:60])

    cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["json", "sqlite", "postgres", "all"], default="all")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.engine import Database
    from app.db.schema import migrate
    from app.services.state import JsonState, SqlState

    tmp_root = Path(tempfile.mkdtemp(prefix="einstein_state_"))
    settings = get_settings()
    backends = ["json", "sqlite", "postgres"] if args.only == "all" else [args.only]

    try:
        for backend in backends:
            if backend == "json":
                def make(domain, root=tmp_root):
                    return JsonState(root / f"{domain}.json")

                def cleanup(root=tmp_root):
                    shutil.rmtree(root, ignore_errors=True)
                    root.mkdir(parents=True, exist_ok=True)

                run_backend("json", make, cleanup)
                continue

            if backend == "sqlite":
                path = Path(tempfile.gettempdir()) / "einstein_verify_state.db"
                if path.exists():
                    path.unlink()
                db = Database("sqlite", str(path))

                def drop(db=db):
                    db.execute("DELETE FROM domain_state WHERE domain LIKE 'verify_%'")
                    db.execute("DELETE FROM domain_state WHERE domain IN (%s)"
                               % ", ".join("?" for _ in PROBES), tuple(PROBES))

            else:
                db = Database("postgres", settings.postgres_dsn)

                def drop(db=db):
                    db.execute("DELETE FROM domain_state WHERE domain LIKE 'verify_%%'")
                    db.execute("DELETE FROM domain_state WHERE domain IN (%s)"
                               % ", ".join("?" for _ in PROBES), tuple(PROBES))

            migrate(db)
            drop()

            def make(domain, db=db):
                return SqlState(db, domain)

            run_backend(backend, make, drop)
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
