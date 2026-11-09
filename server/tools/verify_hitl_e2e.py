"""端到端验收：人工确认（暂停 → 批准/拒绝 → 从断点续跑）。

跑法：`python tools/verify_hitl_e2e.py`（需要后端在 127.0.0.1:8000 运行，
且 `APP_AGENT_APPROVAL_TOOLS` 含 `code_run`）

为什么单独做成 HTTP 级验收：`verify_p0_fixes.py` 用脚本化模型验证的是框架
语义，看不到「路由有没有把 approvals.request 写进去」「续跑请求有没有带上
resume 标记」「检查点线程 id 拼得对不对」——这三处任何一处漏了，线上表现
就是"批准后任务从头再来"。所以这里走真实 HTTP + 真实模型，只断言用户能
观察到的事实：

1. 首轮停在 `approval_required`，而不是闷头执行；
2. 别人的/不存在的 session 批准是 404（不能靠猜 session 预批准）；
3. 批准后从断点续跑：工具真的执行了，且没有重做已完成的步骤；
4. 拒绝后工具不执行，拒绝理由回传给模型（模型据此换路线）。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api"
CAPS = [
    "web_search", "web_fetch", "project_create", "fs_write", "fs_read",
    "fs_list", "code_run", "slide_upsert", "slide_lint", "ppt_create",
]

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS ' if ok else 'FAIL '} {name}" + (f" — {detail}" if detail and not ok else ""))


def _post(token: str, path: str, payload: dict, timeout: int = 600):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout)


def login() -> str:
    body = json.dumps({"username": "admin", "password": "admin123"}).encode()
    req = urllib.request.Request(
        f"{BASE}/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["token"]


def run(token: str, payload: dict) -> tuple[list[dict], str, str]:
    """跑一次 /agent/run，返回 (事件列表, session_id, 最终答案)。"""
    resp = _post(token, "/agent/run", payload)
    events: list[dict] = []
    session = payload.get("session_id", "")
    event = ""
    answer = ""
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        if event == "meta":
            session = data.get("session_id", "")
        if event == "delta":
            answer += data.get("text", "")
            continue
        events.append({"event": event, **data})
    return events, session, answer


def kinds(events: list[dict]) -> list[str]:
    out = []
    for e in events:
        if e["event"] == "step":
            out.append(e["step"].get("kind", ""))
        else:
            out.append(e["event"])
    return out


def main() -> int:
    token = login()

    # ---- 1. 首轮必须停下来等确认 ----
    task = "用 code_run 执行 python 打印 42，把输出贴出来"
    events, session, _ = run(token, {"task": task, "tool_ids": CAPS, "kb_ids": []})
    approval = next((e for e in events if e["event"] == "step"
                     and e["step"].get("kind") == "approval_required"), None)
    check("首轮停在 approval_required", approval is not None, str(kinds(events)))
    if approval is None:
        print(
            "\n提示：这条断言需要服务端开启人工确认——在 server/.env 里设\n"
            "  APP_AGENT_APPROVAL_TOOLS=code_run\n"
            "然后重启后端再跑本脚本。若已开启仍失败，检查模型这一轮是否调用了 code_run。"
        )
        return 2
    step = approval["step"]
    check("暂停的是 code_run", step.get("tool") == "code_run", str(step.get("tool")))
    check("暂停帧带参数指纹", bool(step.get("args_hash")), str(step))
    calls_before = sum(1 for e in events if e["event"] == "step"
                       and e["step"].get("kind") == "tool_call")
    check("暂停前没有重复调用 code_run", calls_before <= 1, f"{calls_before} 次")

    # ---- 2. 归属：他人的 session 批准不了 ----
    try:
        _post(token, "/agent/approve",
              {"session_id": "not-my-session-xyz", "tool": "code_run", "decision": "approve"})
        check("未知 session 批准返回 404", False, "竟然 200")
    except urllib.error.HTTPError as exc:
        check("未知 session 批准返回 404", exc.code == 404, f"{exc.code}")

    # ---- 3. 批准 → 从断点续跑 ----
    try:
        _post(token, "/agent/approve", {
            "session_id": session, "tool": "code_run",
            "args_hash": step.get("args_hash", ""), "decision": "approve",
        })
        approved = True
    except urllib.error.HTTPError as exc:
        approved = False
        print("  approve 失败：", exc.code, exc.read()[:200])
    check("批准成功", approved)

    events2, session2, answer2 = run(token, {
        "task": task, "tool_ids": CAPS, "kb_ids": [],
        "session_id": session, "resume": True,
    })
    check("续跑用同一个会话", session2 == session, f"{session2} != {session}")
    ks2 = kinds(events2)
    check("续跑没有再次暂停", "approval_required" not in ks2, str(ks2))
    check("续跑产出最终答案", "done" in ks2, str(ks2))

    results = [e["step"] for e in events2
               if e["event"] == "step" and e["step"].get("kind") == "tool_result"]
    joined = " ".join(str(r.get("content", "")) for r in results)
    check("工具真的执行了（输出含 42）", "42" in joined, joined[:200])
    # 断点续跑的语义证据：续跑那一轮不应再出现「模型重新决定调用 code_run」
    # 之前的所有步骤——只应有 1 次 code_run 调用（就是被批准的那次）
    calls2 = [e["step"] for e in events2
              if e["event"] == "step" and e["step"].get("kind") == "tool_call"
              and e["step"].get("name") == "code_run"]
    check("续跑没有重做调用（code_run ≤1 次）", len(calls2) <= 1, f"{len(calls2)} 次")
    check("答案里带上执行结果", "42" in answer2, answer2[:200])

    # ---- 4. 拒绝：工具不执行，理由回传模型 ----
    events3, session3, _ = run(token, {"task": task, "tool_ids": CAPS, "kb_ids": []})
    appr3 = next((e for e in events3 if e["event"] == "step"
                  and e["step"].get("kind") == "approval_required"), None)
    if appr3 is None:
        check("第二次任务也能暂停", False, str(kinds(events3)))
    else:
        check("第二次任务也能暂停", True)
        _post(token, "/agent/approve", {
            "session_id": session3, "tool": "code_run",
            "args_hash": appr3["step"].get("args_hash", ""), "decision": "reject",
        })
        events4, _, _ = run(token, {
            "task": task, "tool_ids": CAPS, "kb_ids": [],
            "session_id": session3, "resume": True,
            "approval_message": "用户拒绝执行代码，请直接用文字告诉我结果。",
        })
        ks4 = kinds(events4)
        check("拒绝后续跑能收尾", "done" in ks4, str(ks4))
        # 拒绝的语义：框架仍会产出一条 ToolMessage 承载拒绝理由，但工具代码
        # 没有真正跑过 —— 判据是输出里没有代码的执行结果
        content4 = " ".join(
            str(e["step"].get("content", "")) for e in events4
            if e["event"] == "step" and e["step"].get("kind") == "tool_result"
        )
        check("拒绝后工具未真正执行", "42" not in content4 or "拒绝" in content4, content4[:200])

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print("  FAIL:", name)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
