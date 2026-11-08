"""S2.6 后端冒烟测试：技能版本/回滚/zip/试运行 + 专家发布流 + MCP 审批与凭证加密。

用法（后端已启动）：.venv/Scripts/python.exe tools/verify_s26.py
"""

import base64
import io
import json
import sys
import urllib.request
import zipfile

sys.path.insert(0, ".")  # 从 server 目录运行，方便直接引用 app 服务层

from verify_auth import bearer, call


def login():
    code, resp = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert code == 200, (code, resp)
    return bearer(resp["token"])


def upload(auth: dict, filename: str, data: bytes):
    boundary = "----verify-s26-boundary"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    body.write(b"Content-Type: application/zip\r\n\r\n")
    body.write(data)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/skills/import", data=body.getvalue(), method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for k, v in auth.items():
        req.add_header(k, v)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def main() -> None:
    auth = login()

    print("== 1. 技能版本管理与回滚")
    code, skill = call(
        "POST", "/api/skills",
        {"name": "S26版本测试", "description": "v0.1.0 初始", "system_prompt": "第一版提示词", "tools": []},
        auth,
    )
    assert code == 200, (code, skill)
    sid = skill["id"]
    assert skill["version"] == "0.1.0", skill["version"]
    code, skill = call(
        "PUT", f"/api/skills/{sid}",
        {"system_prompt": "第二版提示词"}, auth,
    )
    assert code == 200 and skill["version"] == "0.1.1", (code, skill.get("version"))
    assert any(v["version"] == "0.1.1" for v in skill["versions"]), "编辑后应生成快照"
    code, vers = call("GET", f"/api/skills/{sid}/versions", headers=auth)
    assert code == 200 and len(vers["versions"]) >= 1
    code, rolled = call("POST", f"/api/skills/{sid}/rollback", {"version": "0.1.1"}, auth)
    assert code == 200 and rolled["system_prompt"] == "第二版提示词", rolled
    assert any("回滚" in (v.get("note") or "") for v in rolled["versions"])
    print(f"   0.1.0 → 编辑 0.1.1 → 快照 → 回滚恢复，当前版本 {rolled['version']} ✓")

    print("== 2. 技能 zip 导出 → 导入")
    req = urllib.request.Request(f"http://127.0.0.1:8000/api/skills/{sid}/export")
    for k, v in auth.items():
        req.add_header(k, v)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "application/zip"
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        meta = json.loads(zf.read("skill.json").decode("utf-8"))
        assert meta["system_prompt"] == "第二版提示词"
    code, imported = upload(auth, "s26.zip", data)
    assert code == 200 and imported["system_prompt"] == "第二版提示词", (code, imported)
    assert imported["id"] != sid and imported["source"] == "zip"
    code, _ = call("DELETE", f"/api/skills/{imported['id']}", headers=auth)
    assert code == 200
    print(f"   导出 {len(data)}B → 导入新技能 {imported['id'][:8]}… → 清理 ✓")

    print("== 3. 技能沙箱试运行")
    code, tr = call("POST", f"/api/skills/{sid}/try", {"message": "用一句话介绍你自己"}, auth)
    assert code == 200 and tr.get("reply"), (code, tr)
    print(f"   回复 {len(tr['reply'])} 字 · {tr['elapsed_ms']}ms · 版本 {tr['version']} ✓")

    print("== 4. 专家发布流 draft → testing → live")
    code, expert = call(
        "POST", "/api/experts",
        {"name": "S26发布测试", "description": "发布流验证", "persona": "你是测试专家",
         "greeting": "你好，我可以帮你验证发布流", "suggestions": ["发布流怎么用？", "状态机有哪些状态？"]},
        auth,
    )
    assert code == 200 and expert["status"] == "draft", (code, expert)
    eid = expert["id"]
    code, r = call("POST", f"/api/experts/{eid}/publish", {"status": "live"}, auth)
    assert code == 400, "draft 直接跳 live 应被拒绝"
    code, r = call("POST", f"/api/experts/{eid}/publish", {"status": "testing"}, auth)
    assert code == 200 and r["status"] == "testing", (code, r)
    code, r = call("POST", f"/api/experts/{eid}/publish", {"status": "live"}, auth)
    assert code == 200 and r["status"] == "live" and r["published_at"], (code, r)
    code, published = call("GET", "/api/experts?published=true", headers=auth)
    assert any(e["id"] == eid for e in published), "live 专家应出现在对话页列表"
    code, r = call("POST", f"/api/experts/{eid}/publish", {"status": "testing"}, auth)
    assert code == 200 and r["status"] == "testing", "下架回测试应允许"
    code, _ = call("DELETE", f"/api/experts/{eid}", headers=auth)
    assert code == 200
    print("   状态机拦截非法转移，live 才进对话列表 ✓")

    print("== 5. MCP 凭证加密与工具审批")
    from app.services import mcp as mcp_service

    enc = mcp_service.encrypt_value("sk-secret-token-123")
    assert enc.startswith("enc:v1:") and "sk-secret" not in enc
    assert mcp_service.decrypt_value(enc) == "sk-secret-token-123"
    assert mcp_service.encrypt_value(enc) == enc, "已加密值不应二次加密"
    print(f"   凭证加密 {enc[:22]}… 解密还原 ✓")

    code, server = call(
        "POST", "/api/mcp/servers",
        {"name": "S26凭证测试", "transport": "http", "url": "http://127.0.0.1:1/none",
         "env": {"API_TOKEN": "sk-secret-token-123"}},
        auth,
    )
    assert code == 200, (code, server)
    msid = server["id"]
    assert server["env"].get("API_TOKEN") == "•••", "凭证必须掩码返回"
    mcp_service._servers = None  # 测试进程强制重新从存储加载，验证落盘已加密
    fresh = mcp_service.get_server(msid)
    assert fresh is not None, "服务器应持久化到存储后端"
    assert fresh.env.get("API_TOKEN", "").startswith("enc:v1:"), "落盘必须加密"
    blob_dump = json.dumps({k: v.env for k, v in mcp_service._registry().items()})
    assert "sk-secret" not in blob_dump, "存储中不允许出现明文凭证"

    code, tools = call("POST", f"/api/mcp/servers/{msid}/tools/approve",
                       {"tool": "search", "approved": True}, auth)
    assert code == 200 and tools["approved_tools"] == ["search"], tools.get("approved_tools")
    # 未审批工具走 API 被拒（审计记录在服务端进程内产生）
    code, err = call("POST", f"/api/mcp/servers/{msid}/call",
                     {"tool": "blocked_tool", "arguments": {}}, auth)
    assert code == 400 and "未通过审批" in err["detail"], (code, err)
    code, ok = call("POST", f"/api/mcp/servers/{msid}/call",
                    {"tool": "search", "arguments": {}}, auth)
    assert code in (200, 400, 502), code  # search 已审批，能否连通取决于 mock server
    code, _ = call("DELETE", f"/api/mcp/servers/{msid}", headers=auth)
    assert code == 200
    print("   落盘加密 + 掩码回显 + 白名单拦截未审批工具 ✓")

    print("== 6. 清理与收尾")
    code, _ = call("DELETE", f"/api/skills/{sid}", headers=auth)
    assert code == 200
    audit = call("GET", "/api/audit?limit=20", headers=auth)[1]["entries"]
    assert any(r["method"] == "MCP_CALL" for r in audit), "MCP 调用应留审计"
    print("   测试技能清理完成，MCP_CALL 审计可见 ✓")

    print("\nS2.6 后端全部通过 ✓")


if __name__ == "__main__":
    main()
