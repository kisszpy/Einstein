"""M7 登录与会话冒烟测试。

覆盖：未登录拦截 → 登录成败 → 令牌访问 → 成员权限边界 → 登出/改口令失效 → 审计留痕。
用法（后端已启动）：.venv/Scripts/python.exe tools/verify_auth.py
"""

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _opener.open(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    print("== 1. 未登录一律 401")
    for path in ("/api/kb", "/api/workspaces", "/api/auth/me", "/api/obs/summary"):
        code, _ = call("GET", path)
        assert code == 401, f"{path} 应 401，实际 {code}"
    code, _ = call("GET", "/api/health")
    assert code == 200, "健康检查应保持公开"
    print("   受保护端点 401，/api/health 仍公开 ✓")

    print("== 2. 登录")
    code, bad = call("POST", "/api/auth/login", {"username": "admin", "password": "wrong-pass"})
    assert code == 401, f"错误口令应 401，实际 {code}"
    code, login = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert code == 200, (code, login)
    token = login["token"]
    assert token.startswith("sess-")
    print(f"   错误口令被拒；管理员登录成功，令牌前缀 {token[:9]}…，过期 {login['expires_at']}")

    code, forged = call("GET", "/api/kb", headers=bearer("sess-forged-token"))
    assert code == 401, "伪造令牌应 401"
    code, me = call("GET", "/api/auth/me", headers=bearer(token))
    assert code == 200 and me["user"]["username"] == "admin"
    print(f"   /auth/me → {me['user']['display_name']}（{me['user']['role_label']}），"
          f"可见工作区 {me['workspaces']} ✓")

    print("== 3. 成员权限边界")
    code, ws = call("POST", "/api/workspaces", {"name": f"权限测试-{token[-4:]}"}, bearer(token))
    assert code == 200, (code, ws)
    ws_id = ws["id"]
    username = f"tester{token[-4:]}"
    code, member = call(
        "POST", "/api/users",
        {"username": username, "password": "member123", "display_name": "冒烟成员",
         "role": "member", "workspaces": ["default"]},
        bearer(token),
    )
    assert code == 200, (code, member)
    code, mlogin = call("POST", "/api/auth/login", {"username": username, "password": "member123"})
    assert code == 200, (code, mlogin)
    mtoken = mlogin["token"]
    print(f"   新建工作区 {ws['name']}、成员 {username}（仅 default）")

    code, visible = call("GET", "/api/workspaces", headers=bearer(mtoken))
    assert code == 200 and [w["id"] for w in visible] == ["default"], visible
    print("   成员只看到归属工作区 ✓")

    code, scoped = call("GET", "/api/kb", headers={**bearer(mtoken), "X-Workspace": ws_id})
    assert code == 200, code
    _, admin_scoped = call("GET", "/api/kb", headers={**bearer(token), "X-Workspace": ws_id})
    print(f"   成员请求非归属工作区 → 回落到自己的视图（{len(scoped)} 个库，"
          f"管理员在 {ws['name']} 看到 {len(admin_scoped)} 个）")

    code, denied = call("GET", "/api/users", headers=bearer(mtoken))
    assert code == 403, f"成员查成员列表应 403，实际 {code}"
    code, denied = call("POST", "/api/workspaces", {"name": "越权新建"}, bearer(mtoken))
    assert code == 403, f"成员建工作区应 403，实际 {code}"
    print("   成员越权（成员列表 / 新建工作区）均 403 ✓")

    print("== 4. 登出与改口令使会话失效")
    code, _ = call("POST", "/api/auth/logout", None, bearer(mtoken))
    assert code == 200
    code, _ = call("GET", "/api/auth/me", headers=bearer(mtoken))
    assert code == 401, "登出后令牌应失效"
    print("   登出后旧令牌 401 ✓")

    code, _ = call("POST", "/api/auth/login", {"username": username, "password": "member123"})
    again = _["token"]
    code, _ = call("POST", f"/api/users/{member['id']}/password", {"password": "member456"}, bearer(token))
    assert code == 200
    code, _ = call("GET", "/api/auth/me", headers=bearer(again))
    assert code == 401, "改口令后旧会话应失效"
    code, relogin = call("POST", "/api/auth/login", {"username": username, "password": "member456"})
    assert code == 200, (code, relogin)
    print("   管理员重置口令 → 旧会话失效、新口令可登录 ✓")

    print("== 5. 登录行为进审计")
    code, audit = call("GET", "/api/audit?limit=50", headers=bearer(token))
    rows = [r for r in audit["entries"] if r["path"] == "/api/auth/login"]
    assert any(r["status"] == 401 for r in rows), "失败的登录尝试应留痕"
    assert any(r["status"] == 200 for r in rows), "成功登录应留痕"
    print(f"   审计中登录记录 {len(rows)} 条（含失败尝试）✓")

    print("== 6. 清理")
    call("DELETE", f"/api/users/{member['id']}", headers=bearer(token))
    call("DELETE", f"/api/workspaces/{ws_id}", headers=bearer(token))
    print("   测试成员与工作区已删除")
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
