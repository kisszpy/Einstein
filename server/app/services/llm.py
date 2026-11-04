"""模型接入层：统一走 OpenAI 兼容端点。

llama.cpp server / 云端模型都实现同一接口，切换只改 .env。
聊天流式用 openai 原生客户端（LangChain 会丢弃 reasoning_content，
思考模型的推理流需要精确透传）；Agent 编排仍走 LangChain/LangGraph。
"""

from collections.abc import AsyncIterator

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from app.core.config import get_settings


def get_chat_model() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        streaming=True,
        timeout=120,
        max_retries=1,
    )


def _lc_to_openai_messages(messages: list[BaseMessage]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = {"human": "user", "ai": "assistant", "system": "system"}.get(
            m.type, "user"
        )
        out.append({"role": role, "content": str(m.content)})
    return out


async def stream_chat(
    messages: list[BaseMessage], temperature: float = 0.7
) -> AsyncIterator[tuple[str, str]]:
    """流式产出 (kind, text)：kind 为 "reasoning"（思考）或 "delta"（正文）。

    Qwen3 等思考模型把推理放在 reasoning_content，需要单独下发，
    前端渲染为可折叠的「思考过程」块。
    """
    settings = get_settings()
    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=180,
        max_retries=1,
    )
    try:
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            messages=_lc_to_openai_messages(messages),
            stream=True,
            temperature=temperature,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield "reasoning", reasoning
            if delta.content:
                yield "delta", delta.content
    finally:
        await client.close()
