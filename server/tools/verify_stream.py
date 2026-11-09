"""端到端验收：真流式与断连转后台。

跑法：`python tools/verify_stream.py`（需要后端在 127.0.0.1:8000 运行）

两个断言只有真实 HTTP 才能验：
1. **真流式**：第一个 `delta` 的到达时间必须远早于 `done`。旧实现是「全文生成
   完再按 24 字符切片下发」，首个 delta 与 done 相差不到几十毫秒。
2. **断连转后台**：读到一半关掉连接，任务必须继续跑完并把运行记录落库——
   旧实现里客户端断开 = 生成器被取消 = 任务消失、什么都没留下。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api"
CAPS = [
    "web_search", "web_fetch", "project_create", "fs_write", "fs_read",
    "fs_list", "code_run",
]
LONG_TASK = "用 600 字左右介绍 RAG 检索增强生成的原理，分四小节：切片、嵌入、检索、生成。不要调用任何工具，直接回答。"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS ' if ok else 'FAIL '} {name}" + (f" — {detail}" if detail and not ok else ""))


def login() -> str:
    body = json.dumps({"username": "admin", "password": "admin123"}).encode()
    req = urllib.request.Request(
        f"{BASE}/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["token"]


def open_run(token: str, payload: dict):
    req = urllib.request.Request(
        f"{BASE}/agent/run", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=600)


def get_json(token: str, path: str):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def test_streaming(token: str) -> str:
    """真流式：增量必须在一段时间里持续到达，而不是结束前一次性喷出。

    为什么不用「首字占比」当判据：首字延迟主要取决于模型第一个 token 的时机
    （思考型模型可能先想 7 秒），跟答案长短无关，比例阈值会随机翻车。
    假流式（生成完再按 24 字符切片）的指纹是**所有帧挤在 done 前的几毫秒里**，
    所以看「首帧到末帧的跨度」和「帧数」才稳。
    """
    t0 = time.perf_counter()
    resp = open_run(token, {"task": LONG_TASK, "tool_ids": CAPS, "kb_ids": []})
    event = ""
    first_delta = None
    last_delta = None
    done_at = None
    answer = ""
    deltas = 0
    run_id = ""
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        if event == "meta":
            run_id = data.get("run_id", "")
        elif event == "delta":
            now = time.perf_counter() - t0
            deltas += 1
            answer += data.get("text", "")
            if first_delta is None:
                first_delta = now
            last_delta = now
        elif event == "done":
            done_at = time.perf_counter() - t0

    span = (last_delta - first_delta) if (first_delta is not None and last_delta is not None) else 0.0
    print(
        f"  首帧 {first_delta or 0:.2f}s / 末帧 {last_delta or 0:.2f}s / 完成 {done_at or 0:.2f}s"
        f" / 帧数 {deltas} / 流式跨度 {span:.2f}s / 字数 {len(answer)}"
    )
    check("有 delta 事件", deltas > 0, f"{deltas}")
    check("有 done 事件", done_at is not None)
    if first_delta is None or done_at is None:
        return run_id
    check("首帧早于完成", first_delta < done_at, f"{first_delta:.2f} vs {done_at:.2f}")
    # 真流式的指纹：增量持续了至少 1 秒。假流式时全部帧在 done 前 <0.2s 内喷完
    check("增量持续下发（跨度 > 1s）", span > 1.0, f"跨度 {span:.2f}s")
    check("答案长度合理（>300 字）", len(answer) > 300, f"{len(answer)} 字")
    # 24 字符一切的话 800 字只有 ~35 帧；token 级增量是几百帧
    check("分片是 token 级（帧数 > 100）", deltas > 100, f"{deltas} 帧")
    return run_id


def test_disconnect(token: str) -> None:
    """断连转后台：读几帧就关掉连接，任务应在后台跑完并落库。"""
    before = {r["id"] for r in get_json(token, "/agent/runs")}
    resp = open_run(token, {"task": LONG_TASK, "tool_ids": CAPS, "kb_ids": []})
    read = 0
    for raw in resp:
        if raw.startswith(b"data: "):
            read += 1
            if read >= 2:
                break
    resp.close()          # 客户端走了
    print(f"  已断开（读了 {read} 帧），等待后台跑完…")

    # 等待后台任务完成并落库（最多 90 秒）
    for _ in range(45):
        time.sleep(2)
        runs = get_json(token, "/agent/runs")
        fresh = [r for r in runs if r["id"] not in before]
        if any(r["status"] == "ok" and r.get("answer") for r in fresh):
            done = next(r for r in fresh if r["status"] == "ok" and r.get("answer"))
            check("断连后任务仍完成并落库", True)
            check("断连后答案已保存（>200 字）", len(done["answer"]) > 200, f"{len(done['answer'])} 字")
            return
    check("断连后任务仍完成并落库", False, "90 秒内没有出现完成的运行记录")


def main() -> int:
    token = login()
    test_streaming(token)
    test_disconnect(token)
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print("  FAIL:", name)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
