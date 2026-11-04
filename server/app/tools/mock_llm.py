"""Mock OpenAI 兼容端点：llama.cpp 未启动时用于前后端联调。

支持两种模式：
1. 普通对话：按 2 字符切片流式回复
2. 工具调用（body 带 tools 时）：第一次返回 calculator tool_calls，
   收到 tool 结果后返回最终回答——模拟 ReAct 循环，验证 Agent 链路

用法:
    .venv/Scripts/python -m app.tools.mock_llm   # 跑在 8080
"""

import asyncio
import json
import re

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="Mock LLM (OpenAI compatible)")

REPLY = (
    "这是 **mock 模型** 的流式回复：说明 SSE 链路已经打通。\n\n"
    "```python\nprint('hello from mock')\n```\n\n"
    "把 `.env` 里的 `APP_LLM_BASE_URL` 指向 llama.cpp 后即可切换真实模型。"
)


@app.get("/v1/models")
async def models() -> dict:
    return {"data": [{"id": "mock-model", "object": "model"}]}


def _extract_expression(text: str) -> str:
    """从用户消息里抠出四则运算表达式，抠不到就默认。"""
    m = re.search(r"[0-9(][0-9+\-*/().\s]*[0-9)]", text)
    return m.group(0).strip() if m else "1+1"


@app.post("/v1/chat/completions")
async def chat_completions(body: dict):
    tools = body.get("tools")
    if tools:
        return _tool_call_response(body)

    async def gen():
        for i in range(0, len(REPLY), 2):
            chunk = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": REPLY[i : i + 2]}, "index": 0}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.02)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _tool_call_response(body: dict) -> dict:
    messages = body.get("messages", [])
    has_tool_result = any(m.get("role") == "tool" for m in messages)

    if has_tool_result:
        tool_content = next(
            (m.get("content", "") for m in messages if m.get("role") == "tool"), ""
        )
        content = (
            f"我已经通过计算器得到了结果：{tool_content}。"
            f"这就是该表达式的准确计算结果，无需再验算。"
        )
        message: dict = {"role": "assistant", "content": content}
        finish = "stop"
    else:
        user_text = next(
            (m["content"] for m in messages if m.get("role") == "user"), ""
        )
        expr = _extract_expression(user_text)
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_mock_1",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": json.dumps({"expression": expr}, ensure_ascii=False),
                    },
                }
            ],
        }
        finish = "tool_calls"

    return {
        "id": "mock-completion",
        "object": "chat.completion",
        "model": body.get("model", "mock-model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
