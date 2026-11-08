"""S2.5 后端冒烟测试：会话管理（重命名/置顶/归档/搜索）+ 反馈 + 长期记忆。

用法（后端已启动）：.venv/Scripts/python.exe tools/verify_s25.py
"""

import urllib.parse

from verify_auth import bearer, call

MARK = "S25验证标记-8f3c"


def main() -> None:
    print("== 1. 登录")
    code, login = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert code == 200, (code, login)
    auth = bearer(login["token"])
    print("   admin 登录成功 ✓")

    print("== 2. 会话列表与目标选取")
    code, convs = call("GET", "/api/conversations", headers=auth)
    assert code == 200, (code, convs)
    assert convs, "需要至少一个既有会话才能验证 PATCH"
    target = convs[0]
    conv_id, orig_title = target["id"], target["title"]
    print(f"   选取会话 {conv_id[:8]}…（{len(convs)} 个未归档会话）")

    print("== 3. 重命名 + 置顶同时生效（修复后的互斥 bug）")
    code, patched = call(
        "PATCH", f"/api/conversations/{conv_id}",
        {"title": MARK, "pinned": True}, auth,
    )
    assert code == 200, (code, patched)
    assert patched["title"] == MARK and patched["pinned"] is True, patched
    print(f"   PATCH title+pinned → {patched['title']} / pinned={patched['pinned']} ✓")

    print("== 4. 全文搜索命中（标题）")
    qs = urllib.parse.quote(MARK)
    code, hits = call("GET", f"/api/conversations?q={qs}", headers=auth)
    assert code == 200, (code, hits)
    assert any(h["id"] == conv_id and h["pinned"] for h in hits), hits
    print(f"   q={MARK} 命中 {len(hits)} 条，置顶位在首 ✓")

    print("== 5. 归档与过滤")
    code, patched = call(
        "PATCH", f"/api/conversations/{conv_id}", {"archived": True, "pinned": False}, auth,
    )
    assert code == 200 and patched["archived"] is True, patched
    code, default_list = call("GET", "/api/conversations", headers=auth)
    assert all(c["id"] != conv_id for c in default_list), "归档后不应出现在默认列表"
    code, arch_list = call("GET", "/api/conversations?archived=true", headers=auth)
    assert any(c["id"] == conv_id for c in arch_list), "归档列表应包含该会话"
    print(f"   归档后默认列表隐藏、archived=true 可见（{len(arch_list)} 个归档会话）✓")

    print("== 6. 恢复原状")
    code, _ = call(
        "PATCH", f"/api/conversations/{conv_id}",
        {"title": orig_title, "archived": False}, auth,
    )
    assert code == 200
    print(f"   标题恢复为「{orig_title}」，取消归档 ✓")

    print("== 7. 长期记忆 CRUD 与检索")
    code, mem = call("POST", "/api/memory", {"content": "S25 验证记忆：项目代号星辰计划"}, auth)
    assert code == 200, (code, mem)
    mid = mem["id"]
    qs = urllib.parse.quote("星辰")
    code, hits = call("GET", f"/api/memory?q={qs}", headers=auth)
    assert code == 200 and any(h["id"] == mid for h in hits), (code, hits)
    code, _ = call("DELETE", f"/api/memory/{mid}", headers=auth)
    assert code == 200
    code, after = call("GET", f"/api/memory?q={qs}", headers=auth)
    assert all(h["id"] != mid for h in after), "删除后不应再命中"
    print(f"   添加 → 关键词命中 → 删除（删后 {len(after)} 条）✓")

    print("== 8. 赞踩反馈入库与汇总")
    code, fb = call(
        "POST", "/api/feedback",
        {"rating": "up", "reason": "S25 冒烟验证", "turn": 1}, auth,
    )
    assert code == 200, (code, fb)
    code, fbl = call("GET", "/api/feedback", headers=auth)
    assert code == 200 and fbl["summary"].get("up", 0) >= 1, fbl
    print(f"   汇总 {fbl['summary']}，最近一条 reason={fbl['entries'][0]['reason']!r} ✓")

    print("\nS2.5 后端全部通过 ✓")


if __name__ == "__main__":
    main()
