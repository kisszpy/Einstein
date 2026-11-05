"""一次性长任务实跑：联网调研 -> research.md -> PPT。

用法：python tools/run_long_task.py "任务文本" [项目名]
逐条打印工具调用与结果摘要，最后打印产物清单与项目列表。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TOOL_IDS = [
    "web_search", "web_fetch", "project_create",
    "fs_write", "fs_read", "fs_list", "code_run", "ppt_create",
]


def call(path: str, payload: dict | None = None, token: str | None = None,
         method: str | None = None, timeout: int = 60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("POST" if data else "GET")
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return OPEN.open(req, timeout=timeout)


def say(line: str, t0: float) -> None:
    print(f"[{time.perf_counter() - t0:6.1f}s] {line}", flush=True)


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "调研三个 AI Agent 框架的差异，写 research.md 并做 6 页 PPT"
    project = sys.argv[2] if len(sys.argv) > 2 else ""
    with call("/api/auth/login", {"username": "admin", "password": "admin123"}) as resp:
        token = json.load(resp)["token"]

    payload = {"task": task, "tool_ids": TOOL_IDS, "kb_ids": [], "project": project}
    t0 = time.perf_counter()
    steps = artifacts = 0
    with call("/api/agent/run", payload, token, timeout=1800) as resp:
        event = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
                continue
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            if event == "step":
                step = data["step"]
                if step["kind"] == "tool_call":
                    say(f"-> {step['name']} {str(step['args'])[:200]}", t0)
                else:
                    say(f"<- {step.get('name', step.get('kind'))} ({step.get('duration_ms')}ms) {str(step['content'])[:200]}", t0)
                    steps += 1
            elif event == "artifact":
                artifacts += 1
                say(f"** artifact {data['artifact']}", t0)
            elif event == "answer_start":
                print("\n----- 回答 -----", flush=True)
            elif event == "delta":
                sys.stdout.write(data["text"])
                sys.stdout.flush()
            elif event == "done":
                print(f"\n----- done project={data.get('project')} artifacts={data.get('artifacts')}", flush=True)
            elif event == "error":
                print(f"\n!!!! error {data}", flush=True)
                return 1

    say(f"用时 {time.perf_counter() - t0:.1f}s，工具结果 {steps} 个，产物 {artifacts} 个", t0)
    with call("/api/projects", None, token) as resp:
        print("项目:", [p["project"] for p in json.load(resp)], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
