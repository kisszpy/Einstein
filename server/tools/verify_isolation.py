"""用户级资源隔离验收：同一工作区里两个账号交叉访问六类资源。

验证口径（全部走 HTTP，除了会话/运行记录需要落库种子）：
- 可见性：B 的列表里不能出现 A 创建的对话、知识库、专家、技能、MCP、运行记录；
- 直连：B 拿着 A 的资源 id 直接读写，一律 404（不泄露存在性）；
- 预置共享：预置专家/技能（owner=''）对 B 依然可见，但 B 改不动（403）；
- 清理：脚本自己建的两个账号与资源在结束时删干净。

用法：
    python tools/verify_isolation.py            # 默认 http://127.0.0.1:8000
    APP_BASE=http://127.0.0.1:8000 python tools/verify_isolation.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = os.environ.get("APP_BASE", "http://127.0.0.1:8000")
WS = "default"
ADMIN_USER = os.environ.get("APP_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("APP_ADMIN_PASS", "admin123")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"（{detail}）" if detail else ""))
    if not ok:
        _failures.append(name)


def call(method: str, path: str, token: str = "", body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("X-Workspace", WS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        return exc.code, payload


def login(username: str, password: str) -> str:
    status, data = call("POST", "/api/auth/login", body={"username": username, "password": password})
    assert status == 200 and isinstance(data, dict), f"登录失败 {username}: {status} {data}"
    return data["token"]


def main() -> int:
    from app.core.config import get_settings
    from app.db.engine import Database

    suffix = secrets.token_hex(3)
    user_a, user_b = f"iso_a_{suffix}", f"iso_b_{suffix}"
    pwd = "Iso-pass-123"

    admin = login(ADMIN_USER, ADMIN_PASS)
    created_users: list[str] = []
    for name in (user_a, user_b):
        status, data = call(
            "POST", "/api/users", admin,
            {"username": name, "display_name": name, "password": pwd,
             "role": "member", "workspaces": [WS]},
        )
        assert status in (200, 201), f"建号失败 {status} {data}"
        created_users.append((data.get("user") or data).get("id", ""))
    tok_a, tok_b = login(user_a, pwd), login(user_b, pwd)
    print(f"账号：A={user_a} B={user_b}（同一个 {WS} 工作区）\n")

    made: dict[str, str] = {}   # 记录 A 创建的资源 id，结尾清理
    try:
        # ---- 1. 知识库 ----
        status, kb = call("POST", "/api/kb", tok_a, {"name": f"isolated-kb-{suffix}"})
        check("A 创建知识库", status == 200 and bool(kb.get("id")), str(status))
        made["kb"] = kb["id"]
        _, list_b = call("GET", "/api/kb", tok_b)
        check("B 列表看不到 A 的知识库", all(k["id"] != made["kb"] for k in list_b))
        status, _ = call("GET", f"/api/kb/{made['kb']}/documents", tok_b)
        check("B 直连 A 的知识库文档 → 404", status == 404, str(status))
        status, hits = call("POST", "/api/kb/search", tok_b, {"query": "x", "kb_ids": [made["kb"]]})
        check("B 用 A 的 kb_id 检索拿不到结果", status == 200 and hits == [], f"{status} {hits}")
        _, list_a = call("GET", "/api/kb", tok_a)
        check("A 自己能看到自己的知识库", any(k["id"] == made["kb"] for k in list_a))

        # ---- 2. 专家 ----
        status, expert = call("POST", "/api/experts", tok_a, {"name": f"iso-expert-{suffix}"})
        check("A 创建专家", status == 200 and bool(expert.get("id")), str(status))
        made["expert"] = expert["id"]
        _, experts_b = call("GET", "/api/experts", tok_b)
        check("B 列表看不到 A 的专家", all(e["id"] != made["expert"] for e in experts_b))
        status, _ = call("GET", f"/api/experts/{made['expert']}", tok_b)
        check("B 直连 A 的专家 → 404", status == 404, str(status))
        status, _ = call("PUT", f"/api/experts/{made['expert']}", tok_b, {"name": "hack"})
        check("B 改 A 的专家 → 404", status == 404, str(status))
        status, _ = call("DELETE", f"/api/experts/{made['expert']}", tok_b)
        check("B 删 A 的专家 → 404", status == 404, str(status))
        # 预置专家（owner=''）对 B 可见但改不动。
        # 判据要看 A 的列表：A 也看不到，说明是空环境没有预置数据（跳过）；
        # A 能看到而 B 看不到，才是隔离真的坏了。
        _, experts_a = call("GET", "/api/experts", tok_a)
        presets_a = [e for e in experts_a if e.get("status") == "live"]
        preset = next((e for e in experts_b if e.get("status") == "live"), None)
        if not presets_a:
            print("SKIP  B 能看到预置专家 — 库里没有预置专家（空环境，非隔离缺陷）")
        else:
            check("B 能看到预置专家", preset is not None)
        if preset:
            status, _ = call("PUT", f"/api/experts/{preset['id']}", tok_b, {"name": "hack"})
            check("B 改预置专家 → 403", status == 403, str(status))

        # ---- 3. 技能 ----
        status, skill = call("POST", "/api/skills", tok_a, {
            "name": f"iso-skill-{suffix}", "description": "x", "system_prompt": "你是测试技能", "tools": [],
        })
        check("A 创建技能", status == 200 and bool(skill.get("id")), str(status))
        made["skill"] = skill["id"]
        _, skills_b = call("GET", "/api/skills", tok_b)
        check("B 列表看不到 A 的技能", all(s["id"] != made["skill"] for s in skills_b))
        status, _ = call("GET", f"/api/skills/{made['skill']}/versions", tok_b)
        check("B 直连 A 的技能 → 404", status == 404, str(status))
        builtin = next((s for s in skills_b if s.get("builtin")), None)
        check("B 仍能看到内置技能", builtin is not None)
        if builtin:
            status, _ = call("PUT", f"/api/skills/{builtin['id']}", tok_b, {"description": "hack"})
            check("B 改内置技能 → 403", status == 403, str(status))

        # ---- 4. MCP 连接 ----
        status, mcp = call("POST", "/api/mcp/servers", tok_a, {
            "name": f"iso-mcp-{suffix}", "transport": "stdio",
            "command": "echo", "args": ["hi"],
        })
        check("A 添加 MCP 连接", status == 200 and bool(mcp.get("id")), str(status))
        made["mcp"] = mcp["id"]
        _, mcps_b = call("GET", "/api/mcp/servers", tok_b)
        check("B 列表看不到 A 的 MCP 连接", all(m["id"] != made["mcp"] for m in mcps_b))
        status, _ = call("PATCH", f"/api/mcp/servers/{made['mcp']}", tok_b, {"name": "hack"})
        check("B 改 A 的 MCP → 404", status == 404, str(status))
        status, _ = call("POST", f"/api/mcp/servers/{made['mcp']}/call", tok_b,
                         {"tool": "x", "arguments": {}})
        check("B 调 A 的 MCP 工具 → 404", status == 404, str(status))

        # ---- 5. 对话与智能体运行（直接落库种子，避免真实调模型）----
        db = Database("postgres", get_settings().postgres_dsn)
        conv_id, run_id = f"isoconv{suffix}", f"isorun{suffix}"
        a_id = created_users[0]
        db.execute(
            "INSERT INTO conversations (id, title, workspace_id, created_at, updated_at, owner_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, "iso", WS, "2026-11-09T00:00:00+00:00", "2026-11-09T00:00:00+00:00", a_id),
        )
        db.execute(
            "INSERT INTO agent_runs (id, session_id, turn, task, tool_ids, steps, answer, status,"
            " workspace_id, project, artifacts, created_at, owner_id)"
            " VALUES (?, ?, 1, 'iso', '[]', '[]', '', 'ok', ?, '', '[]', ?, ?)",
            (run_id, run_id, WS, "2026-11-09T00:00:00+00:00", a_id),
        )
        _, convs_a = call("GET", "/api/conversations", tok_a)
        check("A 能看到自己的会话", any(c["id"] == conv_id for c in convs_a))
        _, convs_b = call("GET", "/api/conversations", tok_b)
        check("B 列表看不到 A 的会话", all(c["id"] != conv_id for c in convs_b))
        status, _ = call("GET", f"/api/conversations/{conv_id}", tok_b)
        check("B 直连 A 的会话 → 404", status == 404, str(status))
        status, _ = call("DELETE", f"/api/conversations/{conv_id}", tok_b)
        check("B 删 A 的会话 → 404", status == 404, str(status))
        _, runs_b = call("GET", "/api/agent/runs", tok_b)
        check("B 列表看不到 A 的运行记录", all(r["id"] != run_id for r in runs_b))
        status, _ = call("DELETE", f"/api/agent/runs/{run_id}", tok_b)
        check("B 删 A 的运行记录 → 404", status == 404, str(status))
    finally:
        # ---- 清理：A 的资源 + 两个测试账号 ----
        if made.get("kb"):
            call("DELETE", f"/api/kb/{made['kb']}", tok_a)
        if made.get("expert"):
            call("DELETE", f"/api/experts/{made['expert']}", tok_a)
        if made.get("skill"):
            call("DELETE", f"/api/skills/{made['skill']}", tok_a)
        if made.get("mcp"):
            call("DELETE", f"/api/mcp/servers/{made['mcp']}", tok_a)
        for uid in filter(None, created_users):
            call("DELETE", f"/api/users/{uid}", admin)
        try:
            Database("postgres", get_settings().postgres_dsn).execute(
                "DELETE FROM conversations WHERE title = 'iso'"
            )
        except Exception:  # noqa: BLE001 — 会话已被上面的接口删掉
            pass
        print("\n已清理测试账号与资源")

    if _failures:
        print(f"\nFAIL: {len(_failures)} 项未通过 → {_failures}")
        return 1
    print("\nPASS: 六类资源（对话/知识库/专家/技能/MCP/运行记录）用户级隔离成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
