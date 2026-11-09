"""跨重启续跑验收：暂停 → **杀进程** → 起进程 → 批准 → 继续跑。

为什么要单独做这一条：`verify_hitl_e2e.py` 的续跑发生在同一个进程里，内存
saver 也能通过。而「短记忆有没有落盘」这件事，只有在进程真的死过一次之后
才看得出来——落盘了就从断点继续，没落盘引擎会明确告诉你"按批准重新执行"。

跑法（两段，中间自己重启后端）：

    # 1. 启动后端（审批开、检查点落 sqlite）
    APP_AGENT_APPROVAL_TOOLS=code_run APP_AGENT_CHECKPOINT=sqlite \\
        python -m uvicorn app.main:app --port 8000

    python tools/verify_checkpoint_restart.py --phase 1
    # 杀掉后端，用**同样的配置**重新启动
    python tools/verify_checkpoint_restart.py --phase 2

phase 1 把会话 id 与暂停帧写进临时文件，phase 2 读回来接着批。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
CAPS = [
    "web_search", "web_fetch", "project_create", "fs_write", "fs_read",
    "fs_list", "code_run", "slide_upsert", "slide_lint", "ppt_create",
]
TASK = "用 code_run 执行 python 打印 42，把输出贴出来"
STATE_FILE = Path(tempfile.gettempdir()) / "einstein_ckpt_restart.json"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS ' if ok else 'FAIL '} {name}" + (f" — {detail}" if detail and not ok else ""))


def _request(token: str, path: str, payload: dict | None = None, method: str = "POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    return urllib.request.urlopen(req, timeout=600)


def login() -> str:
    body = json.dumps({"username": "admin", "password": "admin123"}).encode()
    req = urllib.request.Request(
        f"{BASE}/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["token"]


def checkpoint_info(token: str) -> dict:
    try:
        with _request(token, "/obs/checkpoints", method="GET") as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 — 端点不可用不该让验收直接崩
        return {"error": f"{type(exc).__name__}: {exc}"}


def run(token: str, payload: dict) -> tuple[list[dict], str, str]:
    resp = _request(token, "/agent/run", payload)
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
            session = data.get("session_id", "") or session
        if event == "delta":
            answer += data.get("text", "")
            continue
        events.append({"event": event, **data})
    return events, session, answer


def kinds(events: list[dict]) -> list[str]:
    return [
        e["step"].get("kind", "") if e["event"] == "step" else e["event"]
        for e in events
    ]


def phase1(token: str) -> int:
    info = checkpoint_info(token)
    print(f"检查点档位：{info}")
    check("检查点已落盘（非 memory）", info.get("mode") in ("sqlite", "postgres"), str(info))
    check("检查点连接已建立", bool(info.get("connected")), str(info))

    events, session, _ = run(token, {"task": TASK, "tool_ids": CAPS, "kb_ids": []})
    approval = next(
        (e for e in events if e["event"] == "step"
         and e["step"].get("kind") == "approval_required"),
        None,
    )
    check("首轮停在 approval_required", approval is not None, str(kinds(events)))
    if approval is None:
        return 2

    STATE_FILE.write_text(
        json.dumps({"session": session, "step": approval["step"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n会话 {session} 已暂停，状态写入 {STATE_FILE}")
    print("下一步：杀掉后端 → 用同样的配置重启 → 跑 --phase 2")
    return _summary()


def phase2(token: str) -> int:
    if not STATE_FILE.exists():
        print(f"没找到 {STATE_FILE}，先跑 --phase 1")
        return 2
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    session = state["session"]
    step = state["step"]

    info = checkpoint_info(token)
    print(f"重启后检查点档位：{info}")
    check("重启后仍是落盘档", info.get("mode") in ("sqlite", "postgres"), str(info))

    # 审批记录在 DB 里，重启后仍然有效——这是「批了但服务正好重启」的真实场景
    try:
        _request(token, "/agent/approve", {
            "session_id": session, "tool": step.get("tool", "code_run"),
            "args_hash": step.get("args_hash", ""), "decision": "approve",
        })
        check("重启后批准成功", True)
    except urllib.error.HTTPError as exc:
        check("重启后批准成功", False, f"{exc.code}: {exc.read()[:200]}")
        return _summary()

    events, session2, _ = run(token, {
        "task": TASK, "tool_ids": CAPS, "kb_ids": [],
        "session_id": session, "resume": True,
    })
    check("续跑用同一个会话", session2 == session, f"{session2} != {session}")

    ks = kinds(events)
    notes = " ".join(
        str(e["step"].get("content", "")) for e in events
        if e["event"] == "step" and e["step"].get("kind") == "note"
    )
    # 这条 note 就是「检查点没读到、退回重跑」的信号——它不该出现
    check(
        "没有退化成「按批准重跑」",
        "没有找到可续跑的检查点" not in notes,
        notes[:200],
    )
    check("续跑没有再次暂停", "approval_required" not in ks, str(ks))
    check("续跑产出最终答案", "done" in ks, str(ks))

    outputs = " ".join(
        str(e["step"].get("content", "")) for e in events
        if e["event"] == "step" and e["step"].get("kind") == "tool_result"
    )
    check("工具真的执行了（输出含 42）", "42" in outputs, outputs[:200])
    STATE_FILE.unlink(missing_ok=True)
    return _summary()


def _summary() -> int:
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 0 if not FAIL else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["1", "2"], required=True)
    args = parser.parse_args()
    token = login()
    return phase1(token) if args.phase == "1" else phase2(token)


if __name__ == "__main__":
    raise SystemExit(main())
