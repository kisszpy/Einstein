"""端到端验收：人工确认（暂停 → 批准/拒绝 → 从断点续跑）。

跑法：`python tools/verify_hitl_e2e.py`（需要后端在 127.0.0.1:8000 运行，
且 `APP_AGENT_APPROVAL_TOOLS` 含 `code_run`）

**先看环境再决定断言哪一套**。要不要问人由执行环境决定（见
`capabilities/code_exec.needs_approval`）：

- 云沙箱 / 容器：不打断，脚本验证"确实没打断且代码真的执行了"；
- 宿主进程，或显式设了 `APP_AGENT_APPROVAL_ISOLATED=true`：验证完整的
  暂停 → 批准 → 续跑 → 拒绝 流程。

为什么单独做成 HTTP 级验收：`verify_p0_fixes.py` 用脚本化模型验证的是框架
语义，看不到「路由有没有把 approvals.request 写进去」「续跑请求有没有带上
resume 标记」「检查点线程 id 拼得对不对」——这三处任何一处漏了，线上表现
就是"批准后任务从头再来"。所以这里走真实 HTTP + 真实模型，只断言用户能
观察到的事实：

1. 首轮停在 `approval_required`，而不是闷头执行（审批模式下）；
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


def _approval_path(token: str) -> int:
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

    # ---- 5. 本次会话都允许：同一对话里后续调用不再询问 ----
    #
    # 这是为了不再"每跑一次点一次"而加的能力。要验的不是按钮能点，而是
    # 授权的边界：它必须对**同一会话**生效，且不能漏到别的会话去。
    events5, session5, _ = run(token, {"task": task, "tool_ids": CAPS, "kb_ids": []})
    appr5 = next((e for e in events5 if e["event"] == "step"
                  and e["step"].get("kind") == "approval_required"), None)
    check("授权前仍会暂停", appr5 is not None, str(kinds(events5)))
    if appr5 is not None:
        _post(token, "/agent/approve", {
            "session_id": session5, "tool": "code_run",
            "args_hash": appr5["step"].get("args_hash", ""),
            "decision": "approve", "scope": "session",
        })
        events6, _, _ = run(token, {
            "task": task, "tool_ids": CAPS, "kb_ids": [],
            "session_id": session5, "resume": True,
        })
        check("授权后续跑完成", "done" in kinds(events6), str(kinds(events6)))

        # 同一会话的新任务：不该再弹
        events7, session7, answer7 = run(token, {
            "task": task, "tool_ids": CAPS, "kb_ids": [], "session_id": session5,
        })
        check("同会话新任务不再暂停", "approval_required" not in kinds(events7),
              str(kinds(events7)))
        check("授权后任务仍正常完成", "done" in kinds(events7), str(kinds(events7)))
        check("授权后结果仍然正确", "42" in answer7, answer7[:200])

        # 换个新对话：授权不外溢
        events8, _, _ = run(token, {"task": task, "tool_ids": CAPS, "kb_ids": []})
        check("新会话重新询问（授权不外溢）",
              any(e["event"] == "step" and e["step"].get("kind") == "approval_required"
                  for e in events8), str(kinds(events8)))

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print("  FAIL:", name)
    return 0 if not FAIL else 1


def _no_approval_path(token: str) -> int:
    """隔离沙箱模式下的验收：**不该打断**，且代码确实跑了。

    这条比「会打断」更容易被写错而无人发现：装配时把 interrupt 去掉的同时
    顺手把 `ctx.hitl` 也置真，工具层就不再自己拦——门是没了，可只要有人以为
    还在，就永远不会去检查。所以要断言"真的执行了"，而不是"没报错"。
    """
    task = "用 code_run 执行 python 打印 42，把输出贴出来"
    events, session, answer = run(token, {"task": task, "tool_ids": CAPS, "kb_ids": []})
    ks = kinds(events)
    check("隔离沙箱不打断（没有 approval_required）", "approval_required" not in ks, str(ks))
    check("任务正常收尾", "done" in ks, str(ks))
    check("会话 id 正常分配", bool(session))
    results = [e["step"] for e in events
               if e["event"] == "step" and e["step"].get("kind") == "tool_result"]
    joined = " ".join(str(r.get("content", "")) for r in results)
    check("工具真的执行了（输出含 42）", "42" in joined, joined[:200])
    check("答案里带上执行结果", "42" in answer, answer[:200])

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print("  FAIL:", name)
    return 0 if not FAIL else 1


def main() -> int:
    # 脚本从任意目录跑都要能读到工程包（tools/ 的上一级才是 server/）
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services.capabilities import code_exec

    token = login()
    mode = code_exec.resolve_mode()
    need = code_exec.needs_approval()
    print(f"执行环境：{mode}    需要人工确认：{'是' if need else '否'}\n")
    if not need:
        print("（隔离沙箱免确认模式。要验收「暂停 → 批准 → 续跑」这套链路，"
              "先在 server/.env 设 APP_AGENT_APPROVAL_ISOLATED=true 并重启后端。）\n")
        return _no_approval_path(token)
    return _approval_path(token)


if __name__ == "__main__":
    sys.exit(main())
