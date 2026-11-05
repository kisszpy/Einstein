"""M6 冒烟测试：工作区隔离 → API Key → 开放 API → 审计。

用法（后端已启动）：
    .venv/Scripts/python.exe tools/verify_m6.py
所有请求绕开系统代理，避免沙箱代理把 127.0.0.1 当成外网。
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
        with _opener.open(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> None:
    print("== 1. 工作区隔离")
    code, ws = call("POST", "/api/workspaces", {"name": "市场部", "description": "冒烟测试用"})
    assert code == 200, (code, ws)
    ws_id = ws["id"]
    print(f"   新建工作区 {ws['name']} ({ws_id})")

    _, default_kbs = call("GET", "/api/kb")
    _, scoped_kbs = call("GET", "/api/kb", headers={"X-Workspace": ws_id})
    print(f"   default 可见知识库 {len(default_kbs)} 个；{ws['name']} 可见 {len(scoped_kbs)} 个")
    assert len(scoped_kbs) == 0, "新工作区不应看到任何知识库"

    code, kb = call("POST", "/api/kb", {"name": "市场部资料"}, {"X-Workspace": ws_id})
    assert code == 200, (code, kb)
    _, default_after = call("GET", "/api/kb")
    assert all(k["id"] != kb["id"] for k in default_after), "默认工作区不该看到市场部的知识库"
    print(f"   市场部建库 {kb['name']}，默认工作区不可见 ✓")

    print("== 2. API Key 签发（默认工作区）")
    code, issued = call("POST", "/api/apikeys", {"name": "冒烟测试密钥"})
    assert code == 200, (code, issued)
    token = issued["token"]
    print(f"   前缀 {issued['key']['prefix']}，明文长度 {len(token)}")

    code, denied = call("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
    assert code == 401, f"无密钥应 401，实际 {code}"
    code, denied = call(
        "POST", "/v1/chat/completions",
        {"messages": [{"role": "user", "content": "hi"}]},
        {"Authorization": "Bearer sk-invalid"},
    )
    assert code == 401, f"伪造密钥应 401，实际 {code}"
    print("   缺失/伪造密钥均被拒 ✓")

    print("== 3. 开放 API 调用")
    auth = {"Authorization": f"Bearer {token}"}
    code, search = call(
        "POST", "/v1/search",
        {"query": "死锁的四个必要条件", "kb_ids": [k for k in []], "top_k": 2},
        auth,
    )
    print(f"   /v1/search 未指定 kb → {len(search.get('hits', []))} 条（预期 0，需显式传 kb_ids）")

    _, kbs = call("GET", "/api/kb")
    target = next((k for k in kbs if k["document_count"] > 0), None)
    if target:
        code, search = call(
            "POST", "/v1/search",
            {"query": "死锁的四个必要条件", "kb_ids": [target["id"]], "top_k": 2},
            auth,
        )
        hits = search.get("hits", [])
        print(f"   /v1/search（{target['name']}）命中 {len(hits)} 条，首条 score={hits[0]['score'] if hits else '-'}")

        # 跨租户越权：市场部的密钥查不到默认工作区的库
        code, other = call("POST", "/api/apikeys", {"name": "市场部密钥"}, {"X-Workspace": ws_id})
        other_auth = {"Authorization": f"Bearer {other['token']}"}
        code, leaked = call(
            "POST", "/v1/search", {"query": "死锁", "kb_ids": [target["id"]], "top_k": 2}, other_auth
        )
        assert leaked.get("hits") == [], "跨工作区检索应被拦"
        print("   跨工作区检索被拦（返回 0 条）✓")

    print("== 4. 审计日志")
    _, audit = call("GET", "/api/audit?limit=10")
    rows = audit["entries"]
    print(f"   共 {audit['summary']['total']} 条，最近：")
    for r in rows[:4]:
        print(
            f"   {r['ts']} {r['key_name'] or '-'} {r['method']} {r['path']} "
            f"→ {r['status']} {r['duration_ms']}ms"
        )
    assert any(r["path"] == "/v1/search" for r in rows), "审计缺失 /v1/search 记录"

    print("== 5. 清理")
    call("DELETE", f"/api/kb/{kb['id']}", headers={"X-Workspace": ws_id})
    call("DELETE", f"/api/workspaces/{ws_id}")
    print("   已删除测试知识库与工作区")
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
