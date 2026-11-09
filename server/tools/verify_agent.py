"""智能体能力冒烟：沙箱/代码/联网/PPT/项目 API + 一次真实 ReAct 运行。

用法：server/.venv/Scripts/python.exe tools/verify_agent.py [--skip-live]
（--skip-live 跳过真实模型与联网的端到端段）
"""

import json
import os
import time
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("EINSTEIN_BASE", "http://127.0.0.1:8000")
# 本机沙箱可能掐断与后端的直连长连接；设 EINSTEIN_BASE=http://localhost:5173
# 可改走前端 dev server 的代理（与浏览器同路径）。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOKEN = ""

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))


def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + urllib.parse.quote(path, safe="/?=&"), method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with _opener.open(req, timeout=120, data=data) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, {}


def main() -> None:
    global TOKEN
    skip_live = "--skip-live" in sys.argv

    code, data = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    check("登录", code == 200)
    TOKEN = data["token"]

    # ---- 项目 API ----
    call("DELETE", "/api/projects/能力验证")  # 清理上次残留（call 内部会统一编码）
    code, created = call("POST", "/api/projects", {"name": "能力验证", "template": "python", "description": "冒烟"})
    check("创建项目", code == 200 and created.get("project"), str(created))
    pid = created.get("project", "")

    code, tree = call("GET", f"/api/projects/{pid}/tree")
    check("文件树", code == 200 and tree.get("count", 0) >= 3, str(tree)[:80])

    code, wrote = call("PUT", f"/api/projects/{pid}/file", {"path": "src/tool.py", "content": "print('ok')"})
    check("写文件", code == 200 and wrote.get("created"), str(wrote))

    code, read = call("GET", f"/api/projects/{pid}/file?path=src/tool.py")
    check("读文件", code == 200 and read.get("text") == "print('ok')")

    # 路径穿越必须被拒
    code, _ = call("GET", f"/api/projects/{pid}/file?path=../../users.json")
    check("穿越防护", code in (400, 404), f"code={code}")

    req = urllib.request.Request(BASE + urllib.parse.quote(f"/api/projects/{pid}/download?path=src/tool.py", safe="/?="))
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with _opener.open(req, timeout=30) as resp:
        body = resp.read()
    check("单文件下载", resp.status == 200 and body == b"print('ok')", f"{resp.status} {body[:40]}")

    req = urllib.request.Request(BASE + urllib.parse.quote(f"/api/projects/{pid}/archive", safe="/"))
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with _opener.open(req, timeout=60) as resp:
        blob = resp.read()
    check("整包下载", resp.status == 200 and blob[:2] == b"PK", f"{resp.status} {len(blob)}B")

    # ---- 代码执行（直接驱动服务层） ----
    sys.path.insert(0, ".")
    from app.services.capabilities import code_exec, sandbox, web  # noqa: E402
    from app.services.capabilities.ppt import build_deck  # noqa: E402

    r = code_exec.run_code("default", pid, code="print(sum(range(10)))")
    check("代码执行", r["ok"] and "45" in r["stdout"], str(r)[:100])
    r = code_exec.run_code("default", pid, code="import time; time.sleep(30)", timeout=5)
    check("超时强杀", not r["ok"] and "超时" in r["stderr"], str(r)[:100])

    # ---- 联网 ----
    result = json.loads(web.web_search("FastAPI 官方文档", 3))
    check("网页搜索", len(result.get("results", [])) >= 1, str(result)[:100])
    page = json.loads(web.web_fetch("https://example.com", 300))
    check("正文抓取", "Example" in page.get("title", ""), str(page)[:80])
    try:
        web.web_fetch("http://127.0.0.1:8000/api/health")
        check("SSRF 防护", False, "未拦截本机地址")
    except web.WebError:
        check("SSRF 防护", True)

    # ---- PPT ----
    deck = build_deck({
        "title": "冒烟测试", "theme": "forest", "filename": "smoke",
        "slides": [
            {"layout": "title", "title": "冒烟测试"},
            {"layout": "bullets", "title": "要点", "bullets": ["a", "b"]},
            {"layout": "table", "title": "表", "headers": ["k", "v"], "rows": [["1", "2"]]},
            {"layout": "end"},
        ],
    })
    check("PPT 构建", deck["slides"] == 4 and deck["layouts"] == ["title", "bullets", "table", "end"], str(deck["layouts"]))

    # ---- 清理 ----
    code, _ = call("DELETE", f"/api/projects/{pid}")
    check("删除项目", code == 200)

    # ---- 真实端到端（模型 + 工具） ----
    if not skip_live:
        print("\n== 端到端（真实模型）==")

        def run_e2e(task: str, tool_ids: list[str]) -> list[tuple[str, dict]]:
            """发起一次端到端运行并收集 SSE 事件。

            用 curl 子进程做传输：沙箱会间歇性掐断 python 的长连接
            （收到前缀字节即 EOF），curl 实测稳定；最多重试 3 次。
            """
            import subprocess
            import tempfile

            body = json.dumps({"task": task, "tool_ids": tool_ids})
            for attempt in range(1, 4):
                with tempfile.NamedTemporaryFile("w+", suffix=".sse", delete=False, encoding="utf-8") as tf:
                    out_path = tf.name
                cmd = [
                    "curl", "-s", "-N", "--noproxy", "*", "-m", "600",
                    "-X", "POST", BASE + "/api/agent/run",
                    "-H", f"Authorization: Bearer {TOKEN}",
                    "-H", "Content-Type: application/json",
                    "--data-binary", body, "-o", out_path,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
                events: list[tuple[str, dict]] = []
                try:
                    with open(out_path, encoding="utf-8") as fh:
                        for block in fh.read().split("\n\n"):
                            ev, dat = "", ""
                            for line in block.split("\n"):
                                if line.startswith("event: "):
                                    ev = line[7:]
                                elif line.startswith("data: "):
                                    dat += line[6:]
                            if dat:
                                events.append((ev, json.loads(dat)))
                finally:
                    os.unlink(out_path)
                done_ev = next((d for e, d in events if e == "done"), None)
                if done_ev is not None:
                    return events
                print(f"    [retry {attempt}] 未收到 done（事件 {len(events)} 个，curl rc={proc.returncode}）")
            return events

        events = run_e2e(
            "创建项目「冒烟端到端」，写 hello.py 打印 1 到 10 的平方，运行验证后汇报。",
            ["project_create", "fs_write", "code_run"],
        )
        names = [s["name"] for e, d in events if e == "step" for s in [d["step"]] if s["kind"] == "tool_call"]
        done = next((d for e, d in events if e == "done"), None)
        if not names or done is None:
            kinds = [(e, (d.get("step") or {}).get("kind") if e == "step" else e) for e, d in events]
            print(f"    [debug] events={kinds[:30]}")
            for e, d in events:
                if e == "error":
                    print(f"    [debug] error={d}")
        # deep 引擎下 fs_* 被 harness 工具取代（SUPERSEDED_TOOLS），且模型可能直接
        # 用 code_run 落盘脚本——写文件是否发生由下面的「端到端产物落盘」断言把关，
        # 这里只要求建了项目、跑了代码
        check("端到端工具调用", {"project_create", "code_run"} <= set(names), str(names))
        check("端到端完成", done is not None)
        if done:
            check("端到端项目归属", done.get("project") == "冒烟端到端", str(done)[:100])
            code, tree = call("GET", f"/api/projects/{done['project']}/tree")
            paths = [x["path"] for x in tree.get("entries", [])]
            check("端到端产物落盘", "hello.py" in paths, str(paths))

        # 路由纪律：纯问答任务即使挂了全部工具，也不应建项目/写代码
        events2 = run_e2e(
            "直接回答：Redis 和 Memcached 做缓存各适合什么场景？只回答，不要创建项目、不要写代码、不要生成任何文件。",
            ["project_create", "fs_write", "code_run"],
        )
        names2 = [s["name"] for e, d in events2 if e == "step" for s in [d["step"]] if s["kind"] == "tool_call"]
        done2 = next((d for e, d in events2 if e == "done"), None)
        check(
            "路由纪律（问答不落工程）",
            done2 is not None and "project_create" not in names2 and "code_run" not in names2,
            f"calls={names2}",
        )

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
