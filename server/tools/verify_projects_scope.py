"""项目可见性验收：工作区里只该出现「用户自己创建的项目」。

背景：早先是「目录即项目」——`data/projects/<ws>/` 下有什么目录，列表就出
什么，于是技能同步目录 `skills/` 被当成项目，用户在文件里看到的是平台自己
的技能文件；项目也没有归属，同一空间的两个成员互相看得见对方的项目。

验收口径（全部走 HTTP）：
1. 运行时目录不外泄：列表里没有 `skills`、没有下划线开头的内部目录；
2. 归属隔离：A 建的项目 B 看不到，B 直连（tree/file/preview/archive/deck/
   写/删）一律 404；
3. 保留名：拿 `skills` 当项目名建 → 400；
4. 存量收养：升级前就存在的项目仍然可见（归空间所有者）。

用法：
    python tools/verify_projects_scope.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from urllib.parse import quote
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


def path_of(pid: str, tail: str = "") -> str:
    """项目名可能是中文，URL 里必须转义。"""
    base = f"/api/projects/{quote(pid, safe='')}"
    return f"{base}/{tail}" if tail else base


def names(rows: object) -> list[str]:
    return [r.get("id", "") for r in rows] if isinstance(rows, list) else []


def main() -> int:
    suffix = secrets.token_hex(3)
    user_a, user_b = f"prj_a_{suffix}", f"prj_b_{suffix}"
    pwd = "Prj-pass-123"
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

    made: list[str] = []
    try:
        # ---- 1. 运行时目录不外泄 ----
        _, rows = call("GET", "/api/projects", admin)
        ids = names(rows)
        check("项目列表不含技能目录 skills", "skills" not in ids, str(ids))
        check("项目列表不含内部目录（_ 开头）", all(not i.startswith("_") for i in ids), str(ids))
        check("项目列表不含 .agent 运行时目录", not any(i.startswith(".") for i in ids), str(ids))
        check("存量项目仍可见（收养生效）", bool(ids), str(ids))

        # ---- 2. A 建项目，B 看不到也进不去 ----
        pname = f"验证项目-{suffix}"
        status, project = call("POST", "/api/projects", tok_a,
                               {"name": pname, "template": "blank", "description": "隔离验收"})
        check("A 创建项目", status == 200 and project.get("project") == pname, f"{status} {project}")
        pid = pname
        made.append(pid)
        check("项目登记在 A 名下", (project or {}).get("owner_id") == created_users[0],
              str((project or {}).get("owner_id")))

        _, rows_a = call("GET", "/api/projects", tok_a)
        check("A 自己能列出该项目", pid in names(rows_a))
        _, rows_b = call("GET", "/api/projects", tok_b)
        check("B 列表看不到 A 的项目", pid not in names(rows_b), str(names(rows_b)))

        status, _ = call("GET", path_of(pid, "tree"), tok_b)
        check("B 直连 A 的项目文件树 → 404", status == 404, str(status))
        status, _ = call("GET", path_of(pid, "file") + "?path=README.md", tok_b)
        check("B 读 A 的项目文件 → 404", status == 404, str(status))
        status, _ = call("GET", path_of(pid, "preview") + "?path=README.md", tok_b)
        check("B 预览 A 的项目文件 → 404", status == 404, str(status))
        status, _ = call("GET", path_of(pid, "archive"), tok_b)
        check("B 打包下载 A 的项目 → 404", status == 404, str(status))
        status, _ = call("GET", path_of(pid, "deck") + "?path=x.pptx", tok_b)
        check("B 读 A 的项目演示文稿 → 404", status == 404, str(status))
        status, _ = call("PUT", path_of(pid, "file"), tok_b,
                         {"path": "hack.md", "content": "x"})
        check("B 写 A 的项目文件 → 404", status == 404, str(status))
        status, _ = call("DELETE", path_of(pid), tok_b)
        check("B 删 A 的项目 → 404", status == 404, str(status))

        # 平台管理员也不越权：项目是个人边界
        _, rows_admin = call("GET", "/api/projects", admin)
        check("管理员列表里也没有别人创建的项目", pid not in names(rows_admin), str(names(rows_admin)))

        # ---- 3. A 自己一切正常 ----
        status, tree = call("GET", path_of(pid, "tree"), tok_a)
        check("A 能读自己的项目文件树", status == 200, str(status))
        status, _ = call("PUT", path_of(pid, "file"), tok_a,
                         {"path": "note.md", "content": "hello"})
        check("A 能写自己的项目文件", status == 200, str(status))

        # ---- 4. 保留名与内部目录占用 ----
        status, _ = call("POST", "/api/projects", tok_a, {"name": "skills"})
        check("用 skills 当项目名 → 400", status == 400, str(status))
        status, _ = call("POST", "/api/projects", tok_a, {"name": "_scratch"})
        check("用 _ 开头的名字建项目 → 400", status == 400, str(status))

        # ---- 5. B 自己的项目互不干扰 ----
        pname_b = f"B的项目-{suffix}"
        status, _ = call("POST", "/api/projects", tok_b, {"name": pname_b})
        check("B 创建自己的项目", status == 200, str(status))
        made.append(pname_b)
        _, rows_a2 = call("GET", "/api/projects", tok_a)
        check("A 列表看不到 B 的项目", pname_b not in names(rows_a2), str(names(rows_a2)))
    finally:
        for pid in made:
            # A 的项目用 A 的令牌删，B 的用 B 的（也顺带验证删除权限）
            token = tok_a if not pid.startswith("B的项目") else tok_b
            call("DELETE", path_of(pid), token)
            call("DELETE", path_of(pid), tok_b)
        for uid in filter(None, created_users):
            call("DELETE", f"/api/users/{uid}", admin)
        print("\n已清理测试账号与项目")

    if _failures:
        print(f"\nFAIL: {len(_failures)} 项未通过 → {_failures}")
        return 1
    print("\nPASS: 项目可见性成立——只列自己创建的项目，运行时目录与别人的项目都不可见")
    return 0


if __name__ == "__main__":
    sys.exit(main())
