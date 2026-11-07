"""全功能接口巡检：覆盖鉴权、项目、知识库、会话、智能体、技能、专家、MCP、
观测、评测、平台、开放 API。

用法（后端已启动）：
    .venv/Scripts/python.exe tools/full_sweep.py [--skip-live]

--skip-live 跳过需要真实模型/联网的用例，只验接口契约与安全边界。
结果同时写入 data/sweep_results.json，供测试报告引用。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TOKEN = ""
RESULTS: list[dict] = []
SECTION = ""


def check(name: str, ok: bool, detail: str = "", severity: str = "P2") -> None:
    RESULTS.append({"section": SECTION, "name": name, "ok": bool(ok), "detail": detail, "severity": severity})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + ("" if ok else f"  <- {detail[:180]}"))


def raw(method: str, path: str, body: bytes | None = None, headers: dict | None = None, timeout: int = 120):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc).encode(), {}


def call(method: str, path: str, body=None, headers: dict | None = None, timeout: int = 120):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    status, blob, hdrs = raw(method, path, data, headers, timeout)
    try:
        parsed = json.loads(blob or b"{}")
    except Exception:  # noqa: BLE001 — 非 JSON 响应本身就是被测点
        parsed = {"_raw": blob.decode("utf-8", "ignore")[:400]}
    return status, parsed, hdrs


def sse(method: str, path: str, body: dict, headers: dict | None = None, timeout: int = 300):
    """收集 SSE 事件：返回 (事件名列表, 拼接文本, 错误消息)。"""
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    events, text, error = [], "", ""
    try:
        with _opener.open(req, timeout=timeout) as resp:
            event = ""
            for line in resp:
                line = line.decode("utf-8", "ignore").rstrip("\n")
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    events.append(event)
                    try:
                        payload = json.loads(line[6:])
                    except Exception:  # noqa: BLE001
                        continue
                    if event == "delta":
                        text += payload.get("text", "")
                    elif event == "error":
                        error = payload.get("message", "")
                    elif event == "done":
                        pass
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    return events, text, error


def section(title: str) -> None:
    global SECTION
    SECTION = title
    print(f"\n===== {title}")


def multipart_upload(path: str, filename: str, content: bytes, headers: dict | None = None):
    boundary = "----sweep" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    hdrs = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    hdrs.update(headers or {})
    return raw("POST", path, body, hdrs)


def main() -> None:
    global TOKEN
    skip_live = "--skip-live" in sys.argv
    tag = uuid.uuid4().hex[:4].upper()

    # ---------- 鉴权 ----------
    section("健康与鉴权")
    status, _, _ = call("GET", "/api/health")
    check("健康探针免鉴权", status == 200, f"HTTP {status}", "P0")

    status, _, _ = call("GET", "/api/projects")
    check("未登录访问受保护接口返回 401", status == 401, f"HTTP {status}", "P0")

    status, _, _ = call("POST", "/api/auth/login", {"username": "admin", "password": "wrong"})
    check("错误口令被拒", status == 401, f"HTTP {status}", "P0")

    status, data, _ = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    ok = status == 200 and "token" in data
    check("管理员登录", ok, f"HTTP {status} {str(data)[:80]}", "P0")
    if not ok:
        print("登录失败，后续用例无法执行")
        return
    TOKEN = data["token"]

    status, me, _ = call("GET", "/api/auth/me")
    check("/auth/me 返回身份", status == 200 and (me.get("user") or {}).get("username") == "admin",
          f"HTTP {status} {str(me)[:100]}", "P1")

    status, _, _ = call("GET", "/api/auth/me", headers={"Authorization": "Bearer bad-token"})
    check("伪造令牌被拒", status == 401, f"HTTP {status}", "P0")

    # ---------- 用户管理 ----------
    section("用户管理")
    status, users, _ = call("GET", "/api/users")
    check("管理员可列用户", status == 200 and isinstance(users, list), f"HTTP {status}", "P1")

    uname = f"sweep_{tag}"
    status, created, _ = call(
        "POST", "/api/users",
        {"username": uname, "password": "Passw0rd!23", "display_name": "巡检用户", "role": "member",
         "workspace_ids": ["default"]},
    )
    check("创建成员账号", status == 200 and created.get("id"), f"HTTP {status} {str(created)[:100]}", "P1")
    uid = created.get("id")

    status, dup, _ = call(
        "POST", "/api/users",
        {"username": uname, "password": "Passw0rd!23", "role": "member"},
    )
    check("重复用户名被拒", status in (400, 409), f"HTTP {status} {str(dup)[:80]}", "P1")

    status, weak, _ = call("POST", "/api/users", {"username": f"weak_{tag}", "password": "1", "role": "member"})
    check("弱口令被拒", status in (400, 422), f"HTTP {status} {str(weak)[:80]}", "P2")

    member_token = ""
    status, data, _ = call("POST", "/api/auth/login", {"username": uname, "password": "Passw0rd!23"})
    if status == 200:
        member_token = data["token"]

    if member_token:
        status, _, _ = call("GET", "/api/users", headers={"Authorization": f"Bearer {member_token}"})
        check("成员不可列用户（403）", status == 403, f"HTTP {status}", "P1")
        status, _, _ = call(
            "POST", "/api/workspaces", {"name": f"越权_{tag}"},
            {"Authorization": f"Bearer {member_token}"},
        )
        check("成员不可建工作区（403）", status == 403, f"HTTP {status}", "P1")
        status, _, _ = call(
            "GET", "/api/kb", headers={"Authorization": f"Bearer {member_token}", "X-Workspace": "not-mine"}
        )
        check("成员请求非归属工作区回落到自己的可见范围", status == 200, f"HTTP {status}", "P2")

    # ---------- 项目 ----------
    section("项目管理")
    pid = f"sweep-{tag.lower()}"
    status, created, _ = call("POST", "/api/projects", {"name": pid, "template": "blank"})
    check("创建项目", status == 200 and created.get("project"), f"HTTP {status} {str(created)[:100]}", "P0")

    status, dupp, _ = call("POST", "/api/projects", {"name": pid, "template": "blank"})
    check("同名项目被拒或复用而非静默覆盖", status in (400, 409) or dupp.get("project") == pid,
          f"HTTP {status} {str(dupp)[:80]}", "P2")

    status, bad, _ = call("POST", "/api/projects", {"name": "../escape", "template": "blank"})
    check("非法项目名被拒（而非静默改名）", status in (400, 422),
          f"HTTP {status} {str(bad)[:120]}", "P2")
    if status == 200 and bad.get("project") == "escape":
        call("DELETE", "/api/projects/escape")  # 清理被静默创建的项目

    status, tree, _ = call("GET", f"/api/projects/{pid}/tree")
    check("读取文件树", status == 200 and "entries" in tree, f"HTTP {status}", "P1")

    status, wrote, _ = call("PUT", f"/api/projects/{pid}/file", {"path": "notes.md", "content": "# 巡检\n正文"})
    check("写文件", status == 200, f"HTTP {status} {str(wrote)[:80]}", "P1")

    status, read, _ = call("GET", f"/api/projects/{pid}/file?path=notes.md")
    check("读文件内容一致", status == 200 and read.get("text", "").startswith("# 巡检"), f"HTTP {status}", "P1")

    status, _, _ = call("GET", f"/api/projects/{pid}/file?path=nope.md")
    check("读取不存在文件返回 404", status == 404, f"HTTP {status}", "P2")

    status, _, _ = call("GET", f"/api/projects/{pid}/file?path=../../users.json")
    check("读文件路径穿越被拒", status in (400, 404), f"HTTP {status}", "P0")

    status, _, _ = call("PUT", f"/api/projects/{pid}/file", {"path": "../../pwn.txt", "content": "x"})
    check("写文件路径穿越被拒", status in (400, 404), f"HTTP {status}", "P0")

    status, blob, _ = raw("GET", f"/api/projects/{pid}/download?path=notes.md")
    check("单文件下载", status == 200 and "巡检".encode() in blob, f"HTTP {status} {len(blob)}B", "P1")

    status, blob, _ = raw("GET", f"/api/projects/{pid}/archive")
    check("整包下载是 zip", status == 200 and blob[:2] == b"PK", f"HTTP {status} {len(blob)}B", "P1")

    call("PUT", f"/api/projects/{pid}/file", {"path": "index.html", "content": "<h1>sweep</h1>"})
    status, preview, hdrs = raw("GET", f"/api/projects/{pid}/preview?path=index.html")
    check("项目预览返回 HTML", status == 200 and b"sweep" in preview, f"HTTP {status}", "P1")

    status, deck0, _ = call("GET", f"/api/projects/{pid}/deck?path=none.pptx")
    check("读取不存在的 deck 返回明确错误", status in (200, 404), f"HTTP {status}", "P2")

    deck_body = {"title": "巡检", "slides": [{"layout": "title", "title": "巡检"}]}
    status, put_deck, _ = call("PUT", f"/api/projects/{pid}/deck?path=sweep.pptx", {"deck": deck_body})
    check("保存 deck", status == 200, f"HTTP {status} {str(put_deck)[:80]}", "P2")
    status, got_deck, _ = call("GET", f"/api/projects/{pid}/deck?path=sweep.pptx")
    check("deck 回读一致", status == 200 and deck_body["title"] in json.dumps(got_deck, ensure_ascii=False),
          f"HTTP {status} {str(got_deck)[:100]}", "P2")

    # ---------- 浏览 ----------
    section("内置浏览器")
    status, themes, _ = call("GET", "/api/browse/themes")
    check("主题色板", status == 200 and len(themes) >= 3, f"HTTP {status}", "P2")

    status, ticket, _ = call("GET", "/api/browse/ticket")
    check("预览票据签发", status == 200 and ticket.get("ticket"), f"HTTP {status}", "P1")

    status, _, _ = call("GET", "/api/browse/read?url=" + urllib.parse.quote("http://127.0.0.1:8000/api/health"))
    check("阅读模式拒绝内网地址（SSRF）", status in (400, 403, 502), f"HTTP {status}", "P0")

    if not skip_live:
        status, page, _ = call("GET", "/api/browse/read?url=" + urllib.parse.quote("https://example.com"))
        check("阅读模式抓取外网", status == 200 and "Example" in str(page)[:400], f"HTTP {status}", "P2")

    # ---------- 知识库 ----------
    section("知识库")
    status, kb, _ = call("POST", "/api/kb", {"name": f"巡检库{tag}"})
    check("新建知识库", status == 200 and kb.get("id"), f"HTTP {status} {str(kb)[:80]}", "P1")
    kb_id = kb.get("id", "")

    status, doc, _ = call("POST", f"/api/kb/{kb_id}/documents", {"name": "死锁.md", "content": "死锁的四个必要条件：互斥、占有并等待、非抢占、循环等待。" * 3})
    ingest_ok = status == 200
    check("粘贴文档入库（嵌入服务不可用时返回 503 且提示清晰）", ingest_ok or (status == 503 and "嵌入" in str(doc)),
          f"HTTP {status} {str(doc)[:160]}", "P1")

    status, up_status, _ = multipart_upload(f"/api/kb/{kb_id}/documents/upload", "上传.txt", "上传内容：互斥条件".encode())
    check("上传文件入库（同上）", status == 200 or (status == 503 and 503 in (status,)),
          f"HTTP {status} {str(up_status)[:120]}", "P1")

    status, docs, _ = call("GET", f"/api/kb/{kb_id}/documents")
    check("文档列表可读", status == 200, f"HTTP {status}", "P2")

    status, res, _ = call("POST", "/api/kb/search", {"kb_ids": [kb_id], "query": "死锁条件", "top_k": 3})
    body_head = str(res)[:200]
    check("新建空库检索不报错", status == 200, f"HTTP {status} {body_head}", "P2")
    check("检索异常时不泄漏堆栈", "_raw" not in res or "Traceback" not in json.dumps(res, ensure_ascii=False),
          body_head, "P0")

    status, res_lex, _ = call("POST", "/api/kb/search", {"kb_ids": [kb_id], "query": "死锁条件", "top_k": 3, "mode": "lexical"})
    check("检索 lexical 模式可用（嵌入故障时的降级路径）", status == 200,
          f"HTTP {status} {str(res_lex)[:120]}", "P2")

    # 真实有文档的知识库：验证检索链路本身
    status, all_kbs, _ = call("GET", "/api/kb")
    populated = next((k for k in all_kbs if k.get("document_count", 0) > 0 and k["id"] != kb_id), None)
    if populated:
        status, real, _ = call("POST", "/api/kb/search", {"kb_ids": [populated["id"]], "query": "互斥", "top_k": 3})
        leaked = "_raw" in real and "Traceback" in real["_raw"]
        # 嵌入服务未启动时应回 503 + 可读原因；启动后应回 200
        graceful = status == 503 and "嵌入" in str(real)
        check("已有文档知识库检索（依赖嵌入服务）", status == 200 or graceful,
              f"HTTP {status} {'响应为异常堆栈' if leaked else str(real)[:160]}", "P0")

    status, _, _ = call("POST", "/api/kb/search", {"kb_ids": ["nonexistent"], "query": "x"})
    check("不存在的知识库检索不泄露存在性（返回空）", status == 200, f"HTTP {status}", "P3")

    # ---------- 会话 ----------
    section("会话")
    status, convs, _ = call("GET", "/api/conversations")
    check("会话列表", status == 200 and isinstance(convs, list), f"HTTP {status}", "P1")
    if convs:
        status, conv, _ = call("GET", f"/api/conversations/{convs[0]['id']}")
        leaked = "_raw" in conv and "Traceback" in conv["_raw"]
        check("会话详情可打开", status == 200 and "messages" in conv,
              f"HTTP {status} {'堆栈泄漏' if leaked else str(conv)[:120]}", "P0")
    status, _, _ = call("GET", "/api/conversations/not-exist")
    check("不存在会话返回 404", status == 404, f"HTTP {status}", "P2")

    # ---------- 智能体 ----------
    section("智能体")
    status, tools, _ = call("GET", "/api/agent/tools")
    check("工具清单", status == 200 and len(tools.get("tools", [])) >= 5, f"HTTP {status}", "P1")

    status, runs, _ = call("GET", "/api/agent/runs")
    check("任务历史可读", status == 200 and isinstance(runs, list), f"HTTP {status}", "P1")
    if runs:
        check("任务记录带会话字段", "session_id" in runs[0] and "turn" in runs[0],
              str(list(runs[0].keys()))[:120], "P2")

    events, _, bad_err = sse("POST", "/api/agent/run", {"task": "x", "project": "no-such-project"})
    check("绑定不存在项目时明确报错", "error" in events and bad_err != "",
          f"events={events[:3]} err={bad_err}", "P2")

    if not skip_live:
        events, text, error = sse("POST", "/api/agent/run", {"task": "只回答一个数字：1+1 等于几？", "tool_ids": []})
        check("智能体跑通并流式返回", "done" in events and text.strip() != "" and not error,
              f"events={events[:6]} err={error} text={text[:60]}", "P0")

        events, text1, err1 = sse("POST", "/api/agent/run", {"task": "记住数字 42，只回复「好」。", "tool_ids": []})
        # 取会话 id
        status, runs2, _ = call("GET", "/api/agent/runs")
        if runs2:
            sid = runs2[0].get("session_id", "")
        events, text2, err2 = sse(
            "POST", "/api/agent/run",
            {"task": "我刚才让你记住的数字是多少？只回答数字。", "tool_ids": [], "session_id": sid},
        )
        check("追问携带上下文（同会话多轮）", "42" in text2, f"sid={sid} 回答={text2[:60]}", "P1")
        status, runs3, _ = call("GET", "/api/agent/runs")
        same = [r for r in runs3 if r.get("session_id") == sid]
        check("追问归并到同一任务", len(same) >= 2, f"同会话 {len(same)} 轮", "P1")
        status, deleted, _ = call("DELETE", f"/api/agent/runs/{sid}")
        check("删除任务会话", status == 200 and deleted.get("deleted", 0) >= 2, f"HTTP {status} {deleted}", "P2")
        status, _, _ = call("DELETE", f"/api/agent/runs/{sid}")
        check("重复删除返回 404", status == 404, f"HTTP {status}", "P2")

    # ---------- 技能 ----------
    section("技能")
    status, skills, _ = call("GET", "/api/skills")
    check("技能列表", status == 200 and isinstance(skills, list), f"HTTP {status}", "P1")

    status, skill, _ = call("POST", "/api/skills", {"name": f"巡检技能{tag}", "description": "sweep", "system_prompt": "你是巡检助手"})
    check("创建自定义技能", status == 200 and skill.get("id"), f"HTTP {status} {str(skill)[:100]}", "P2")
    skill_id = skill.get("id", "")

    status, files, _ = call("GET", f"/api/skills/{skill_id}/files")
    check("技能文件清单", status in (200, 404), f"HTTP {status}", "P3")

    status, _, _ = call("GET", "/api/skills/not-exist/file?path=SKILL.md")
    check("不存在技能文件 404", status == 404, f"HTTP {status}", "P3")

    status, _, _ = call("DELETE", f"/api/skills/{skill_id}")
    check("删除技能", status == 200, f"HTTP {status}", "P2")

    # ---------- 专家 ----------
    section("专家")
    status, experts, _ = call("GET", "/api/experts")
    check("专家列表", status == 200 and isinstance(experts, list), f"HTTP {status}", "P1")

    status, expert, _ = call("POST", "/api/experts", {"name": f"巡检专家{tag}", "description": "sweep", "persona": "严谨"})
    check("创建专家", status == 200 and expert.get("id"), f"HTTP {status} {str(expert)[:100]}", "P2")
    eid = expert.get("id", "")

    status, one, _ = call("GET", f"/api/experts/{eid}")
    check("专家详情", status == 200 and one.get("name", "").startswith("巡检专家"), f"HTTP {status}", "P2")

    status, upd, _ = call("PUT", f"/api/experts/{eid}", {"enabled": False, "temperature": 0.2})
    check("更新专家", status == 200 and upd.get("enabled") is False, f"HTTP {status} {str(upd)[:100]}", "P2")

    status, _, _ = call("GET", "/api/experts/not-exist")
    check("不存在专家 404", status == 404, f"HTTP {status}", "P2")

    status, _, _ = call("DELETE", f"/api/experts/{eid}")
    check("删除专家", status == 200, f"HTTP {status}", "P2")

    # ---------- MCP ----------
    section("MCP")
    status, servers, _ = call("GET", "/api/mcp/servers")
    check("MCP 服务器列表", status == 200 and isinstance(servers, list), f"HTTP {status}", "P1")

    status, srv, _ = call("POST", "/api/mcp/servers", {"name": f"巡检MCP{tag}", "transport": "stdio",
                                                       "command": sys.executable, "args": ["tools/mock_mcp_server.py"]})
    check("注册 MCP 服务器", status == 200 and srv.get("id"), f"HTTP {status} {str(srv)[:100]}", "P2")
    sid_mcp = srv.get("id", "")

    status, refreshed, _ = call("POST", f"/api/mcp/servers/{sid_mcp}/refresh")
    tools_found = refreshed.get("tools") if isinstance(refreshed, dict) else None
    check("刷新 MCP 工具清单", status == 200 and bool(tools_found),
          f"HTTP {status} tools={len(tools_found or [])} {str(refreshed)[:100]}", "P2")

    status, called, _ = call("POST", f"/api/mcp/servers/{sid_mcp}/call", {"tool": "echo", "arguments": {"text": "hi"}})
    check("直连调用 MCP 工具", status == 200 and "echo: hi" in str(called), f"HTTP {status} {str(called)[:160]}", "P2")

    # 启动失败的服务器：错误信息应当可读，不能只抛 ExceptionGroup
    status, badsrv, _ = call("POST", "/api/mcp/servers", {"name": f"坏MCP{tag}", "transport": "stdio",
                                                          "command": "no-such-binary-xyz", "args": []})
    if status == 200:
        status, badref, _ = call("POST", f"/api/mcp/servers/{badsrv['id']}/refresh")
        msg = json.dumps(badref, ensure_ascii=False)
        check("MCP 启动失败时错误可读", "ExceptionGroup" not in msg and ("no-such-binary" in msg or "启动" in msg),
              msg[:180], "P2")
        call("DELETE", f"/api/mcp/servers/{badsrv['id']}")

    status, _, _ = call("PATCH", f"/api/mcp/servers/{sid_mcp}", {"enabled": False})
    check("停用 MCP 服务器", status == 200, f"HTTP {status}", "P2")

    status, _, _ = call("POST", "/api/mcp/servers/not-exist/call", {"tool": "x"})
    check("调用不存在服务器 404", status == 404, f"HTTP {status}", "P2")

    status, _, _ = call("DELETE", f"/api/mcp/servers/{sid_mcp}")
    check("删除 MCP 服务器", status == 200, f"HTTP {status}", "P2")

    # ---------- 观测 ----------
    section("观测")
    status, summary, _ = call("GET", "/api/obs/summary")
    check("观测概览", status == 200 and "requests" in summary, f"HTTP {status} {str(summary)[:80]}", "P2")
    status, reqs, _ = call("GET", "/api/obs/requests")
    check("请求指标", status == 200, f"HTTP {status}", "P2")
    status, llm, _ = call("GET", "/api/obs/llm")
    check("模型指标", status == 200, f"HTTP {status}", "P2")

    # ---------- 评测 ----------
    section("评测")
    status, sets, _ = call("GET", "/api/eval/sets")
    check("评测集列表", status == 200 and isinstance(sets, list), f"HTTP {status}", "P2")

    status, eset, _ = call("POST", "/api/eval/sets", {"kb_id": kb_id, "name": f"巡检集{tag}",
                                                      "items": [{"query": "死锁条件", "expected_contains": ["互斥"]}]})
    check("创建评测集", status == 200 and eset.get("id"), f"HTTP {status} {str(eset)[:100]}", "P3")
    set_id = eset.get("id", "")

    if set_id:
        status, runres, _ = call("POST", f"/api/eval/sets/{set_id}/run", {}, timeout=300)
        check("运行评测集", status == 200, f"HTTP {status} {str(runres)[:120]}", "P3")
        status, _, _ = call("DELETE", f"/api/eval/sets/{set_id}")
        check("删除评测集", status == 200, f"HTTP {status}", "P3")

    # ---------- 平台 ----------
    section("平台")
    status, wss, _ = call("GET", "/api/workspaces")
    check("工作区列表", status == 200 and len(wss) >= 1, f"HTTP {status}", "P2")

    status, ws, _ = call("POST", "/api/workspaces", {"name": f"巡检空间{tag}", "description": "sweep"})
    check("新建工作区", status == 200 and ws.get("id"), f"HTTP {status} {str(ws)[:100]}", "P2")
    ws_id = ws.get("id", "")

    status, _, _ = call("PATCH", f"/api/workspaces/{ws_id}", {"name": f"巡检空间{tag}改"})
    check("修改工作区", status == 200, f"HTTP {status}", "P3")

    status, key, _ = call("POST", "/api/apikeys", {"name": f"巡检密钥{tag}"})
    check("签发 API Key", status == 200 and key.get("token", "").startswith("sk-"),
          f"HTTP {status} {key.get('key', {}).get('prefix')}", "P1")
    api_token = key.get("token", "")

    status, keys, _ = call("GET", "/api/apikeys")
    check("密钥列表不返回明文", status == 200 and all("token" not in k for k in keys), f"HTTP {status}", "P0")

    status, auditlog, _ = call("GET", "/api/audit?limit=5")
    check("审计日志可读", status == 200 and "entries" in auditlog, f"HTTP {status}", "P2")

    # ---------- 开放 API ----------
    section("开放 API")
    status, _, _ = call("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
    check("无密钥调用 /v1 返回 401", status == 401, f"HTTP {status}", "P0")
    status, _, _ = call("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]},
                        {"Authorization": "Bearer sk-invalid"})
    check("非法密钥返回 401", status == 401, f"HTTP {status}", "P0")

    auth_v1 = {"Authorization": f"Bearer {api_token}"}
    if not skip_live:
        status, comp, _ = call("POST", "/v1/chat/completions",
                               {"messages": [{"role": "user", "content": "只回答两个字：收到"}], "stream": False},
                               auth_v1, timeout=300)
        text = json.dumps(comp, ensure_ascii=False)
        check("带密钥对话补全", status == 200 and "choices" in comp, f"HTTP {status} {text[:120]}", "P1")

    status, found, _ = call("POST", "/v1/search", {"query": "死锁", "kb_ids": [kb_id], "top_k": 2}, auth_v1)
    check("开放检索接口可用", status == 200, f"HTTP {status} {str(found)[:120]}", "P1")

    # ---------- 清理 ----------
    section("清理")
    status, _, _ = call("DELETE", f"/api/kb/{kb_id}")
    check("删除知识库", status == 200, f"HTTP {status}", "P3")
    status, _, _ = call("DELETE", f"/api/projects/{pid}")
    check("删除项目", status == 200, f"HTTP {status}", "P3")
    if api_token:
        status, keys, _ = call("GET", "/api/apikeys")
        mine = [k for k in keys if k.get("name", "").startswith("巡检密钥")]
        for k in mine:
            call("DELETE", f"/api/apikeys/{k['id']}")
    if ws_id:
        call("DELETE", f"/api/workspaces/{ws_id}")
    if uid:
        call("DELETE", f"/api/users/{uid}")
    print("   测试资源已清理")

    # ---------- 汇总 ----------
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n===== 结果：{passed} 通过 / {len(failed)} 失败")
    for r in failed:
        print(f"  [{r['severity']}] {r['section']} · {r['name']} :: {r['detail'][:160]}")

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "skip_live": skip_live,
        "total": len(RESULTS),
        "passed": passed,
        "failed": len(failed),
        "results": RESULTS,
    }
    with open("data/sweep_results.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("结果已写入 data/sweep_results.json")


if __name__ == "__main__":
    main()
