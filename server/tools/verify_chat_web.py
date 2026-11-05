"""对话按需联网的回归验证。

跑法：先起后端，然后
    ./.venv/Scripts/python.exe tools/verify_chat_web.py

覆盖：关闭模式不联网、自动模式按需联网并带来源、自动模式识别「翻译/润色」
不属于联网场景、强制模式必联网。传输层用 curl 子进程（python 直连在本机
沙箱会被间歇性掐断，与服务端无关）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def login() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with opener.open(req, timeout=30) as resp:
        return json.load(resp)["token"]


def stream(token: str, body: dict) -> list[tuple[str, dict]]:
    """用 curl 跑一次 SSE，返回 (event, data) 列表。"""
    cmd = [
        "curl", "-s", "--noproxy", "*", "-N", "-m", "240",
        "-X", "POST", BASE + "/api/chat",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
        "--data-binary", json.dumps(body, ensure_ascii=False),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    raw = proc.stdout.decode("utf-8", "ignore")
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        ev, dat = "", ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                dat += line[6:]
        if ev and dat:
            try:
                events.append((ev, json.loads(dat)))
            except json.JSONDecodeError:
                continue
    return events


def answer_of(events: list[tuple[str, dict]]) -> str:
    return "".join(d.get("text", "") for e, d in events if e == "delta")


def web_events(events: list[tuple[str, dict]]) -> list[dict]:
    return [d for e, d in events if e == "web"]


def run_case(token: str, label: str, text: str, mode: str) -> list[tuple[str, dict]]:
    print(f"\n-- {label}（web_mode={mode}）: {text[:40]}")
    events = stream(token, {"messages": [{"role": "user", "content": text}], "web_mode": mode})
    kinds = [e for e, _ in events]
    web = web_events(events)
    print(f"   事件 {len(events)} 个，web 状态 {[w.get('status') for w in web]}")
    print(f"   回答 {len(answer_of(events))} 字")
    check(f"{label}：流正常结束", "done" in kinds and "error" not in kinds, str(kinds[:8]))
    check(f"{label}：回答非空", len(answer_of(events)) > 10)
    return events


def main() -> int:
    token = login()
    print("登录成功")

    print("\n== 1. 关闭模式 ==")
    events = run_case(token, "关闭模式", "今天杭州天气怎么样", "off")
    web = web_events(events)
    check("关闭模式：不发起联网", all(w.get("status") != "searching" for w in web), str(web)[:200])
    check(
        "关闭模式：状态为 skipped/off",
        any(w.get("status") == "skipped" and w.get("reason") == "off" for w in web),
        str(web)[:200],
    )

    print("\n== 2. 自动模式 · 时效问题 ==")
    events = run_case(token, "时效问题", "今天杭州天气怎么样", "auto")
    web = web_events(events)
    done = next((d for d in web if d.get("status") in ("done", "failed")), None)
    check("时效问题：触发了联网", any(w.get("status") == "searching" for w in web), str(web)[:200])
    sources = (done or {}).get("sources") or []
    check("时效问题：拿到来源", len(sources) >= 1, str(done)[:300])
    if sources:
        s0 = sources[0]
        check(
            "时效问题：来源字段完整",
            bool(s0.get("title")) and str(s0.get("url", "")).startswith("http"),
            str(s0)[:200],
        )
    answer = answer_of(events)
    check("时效问题：回答提到天气信息", any(k in answer for k in ("晴", "雨", "温", "°", "C")), answer[:120])

    print("\n== 3. 自动模式 · 翻译任务 ==")
    events = run_case(token, "翻译任务", "把这句话翻译成英文：今天杭州天气很好", "auto")
    web = web_events(events)
    check(
        "翻译任务：没有联网",
        all(w.get("status") != "searching" for w in web),
        str(web)[:200],
    )
    check(
        "翻译任务：判定为不需要（规则/模型）",
        any(w.get("status") == "skipped" for w in web),
        str(web)[:200],
    )

    print("\n== 4. 自动模式 · 需要判定的开放问题 ==")
    events = run_case(token, "开放问题", "最近国内 AI 大模型有什么值得关注的进展？", "auto")
    web = web_events(events)
    print(f"   判定原因：{[w.get('reason') for w in web]}")
    check("开放问题：给出了明确决策", any(w.get("status") in ("done", "skipped", "failed") for w in web))

    print("\n== 5. 强制模式 ==")
    events = run_case(token, "强制联网", "用一句话介绍你自己", "on")
    web = web_events(events)
    check("强制联网：一定发起检索", any(w.get("status") == "searching" for w in web), str(web)[:200])

    print("\n== 6. 非法模式回落自动 ==")
    events = run_case(token, "非法模式", "你好", "whatever")
    check("非法模式：不报错", "error" not in [e for e, _ in events])

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
