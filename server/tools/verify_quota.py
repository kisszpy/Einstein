"""验证智能体的 Token 消耗真的进了日配额账本。

以前只有 `llm.complete`（对话链路）记账，智能体走 ChatOpenAI 直连，
`quota_check` 永远读到 0 —— 配了额度也不拦截。这里直接读 Redis 的计数
前后对比，不看代码看账。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"


def _used(ws: str = "default") -> int:
    """直接读 Redis 里的计数，绕开应用层——账本要能从外面查才算数。"""
    import redis

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    client = redis.Redis.from_url("redis://127.0.0.1:6380/0", socket_timeout=2)
    return int(client.get(f"quota:{ws}:tokens:{today}") or 0)


async def main() -> int:
    async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
        res = await client.post(
            f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"},
        )
        res.raise_for_status()
        headers = {"Authorization": f"Bearer {res.json()['token']}"}

        before = _used()
        body = {
            "task": "用两句话说明什么是 RAG，不要用工具。",
            "tool_ids": [],
            "kb_ids": [],
            "skill_ids": [],
            "project": "",
            "session_id": "",
        }
        answer = ""
        async with client.stream("POST", f"{BASE}/api/agent/run", headers=headers, json=body) as r:
            event = ""
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event == "delta":
                    answer += json.loads(line[5:].strip()).get("text", "")
                elif line.startswith("data:") and event in ("done", "error"):
                    print("[", event, "]", line[5:].strip()[:120])
                    break
        after = _used()

    print(f"回答片段：{answer[:60]}")
    print(f"配额 tokens：{before} → {after}（+{after - before}）")
    ok = after > before
    print("结论：", "PASS（智能体消耗已记账）" if ok else "FAIL（配额没涨）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
