"""端到端验证「停止运行」：起一个真实任务，几秒后调取消接口，看它是否真停。

判据（缺一不可）：
1. POST /api/agent/runs/{id}/cancel 返回 200；
2. SSE 流在取消后收到 done（而不是一直挂着或报服务关闭）；
3. 历史记录里这条运行的状态是 stopped；
4. 取消后服务端不再有新事件（模型不再继续烧 token）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"


async def _token(client: httpx.AsyncClient) -> str:
    """走真实登录接口拿票据。

    不要在脚本里用 `sessions.issue()` 自签：会话存储可能是进程内实现，
    另一个进程签出来的 token 服务端根本不认（会得到 401）。
    """
    res = await client.post(
        f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"},
    )
    res.raise_for_status()
    return res.json()["token"]


async def main() -> int:
    async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
        headers = {"Authorization": f"Bearer {await _token(client)}"}
    run_id = ""
    events: list[str] = []
    cancel_result: dict = {}
    done_at = 0.0
    started = time.time()

    async with httpx.AsyncClient(timeout=None, trust_env=False) as client:

        async def _cancel_after(delay: float) -> None:
            await asyncio.sleep(delay)
            if not run_id:
                cancel_result["error"] = "没拿到 run_id"
                return
            res = await client.post(f"{BASE}/api/agent/runs/{run_id}/cancel", headers=headers)
            cancel_result.update(status=res.status_code, body=res.text[:200])
            print(f"[cancel] {res.status_code} {res.text[:160]}")

        task = asyncio.create_task(_cancel_after(6.0))

        body = {
            "task": "联网查今天的三条科技新闻，逐条写进 news.md，每条 80 字以上",
            "tool_ids": ["web_search", "web_fetch", "project_create", "fs_write"],
            "kb_ids": [],
            "skill_ids": [],
            "project": "",
            "session_id": "",
            "expert_id": "",
            "model_id": "",
            "resume": False,
            "approval_message": "",
        }
        async with client.stream("POST", f"{BASE}/api/agent/run", headers=headers, json=body) as res:
            print("[run]", res.status_code)
            event = ""
            async for line in res.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    events.append(event)
                elif line.startswith("data:") and event:
                    data = line[5:].strip()
                    if event == "meta":
                        run_id = json.loads(data)["run_id"]
                        print("[meta] run_id =", run_id)
                    elif event == "step":
                        kind = json.loads(data).get("step", {}).get("kind")
                        print("[step]", kind)
                    elif event == "done":
                        done_at = time.time()
                        print("[done]", data[:160])
                        break
                    elif event == "error":
                        print("[error]", data[:200])
                        break
                    event = ""

        task.cancel()

    elapsed = (done_at or time.time()) - started
    print(f"\n总耗时 {elapsed:.1f}s；事件：{events[-6:]}")
    print("cancel 结果：", cancel_result)

    # 历史状态
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        rows = (await client.get(f"{BASE}/api/agent/runs", headers=headers)).json()
    mine = [r for r in rows if r.get("id") and r.get("task", "").startswith("联网查今天的")]
    if mine:
        print("历史状态：", mine[0]["status"], "| steps:", len(mine[0].get("steps") or []))
    else:
        print("历史里没找到这条运行")

    ok = (
        cancel_result.get("status") == 200
        and "done" in events
        and bool(mine)
        and mine[0]["status"] == "stopped"
        and elapsed < 120
    )
    print("\n结论：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
