"""模型接入层：统一走 OpenAI 兼容端点。

llama.cpp server / 云端模型都实现同一接口，切换只改 .env。
聊天流式用 openai 原生客户端（LangChain 会丢弃 reasoning_content，
思考模型的推理流需要精确透传）；Agent 编排仍走 LangChain/LangGraph。
"""

from collections.abc import AsyncIterator
import time

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.services import metrics


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


def _client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=180,
        max_retries=1,
    )


async def stream_chat(
    messages: list[BaseMessage], temperature: float = 0.7
) -> AsyncIterator[tuple[str, str]]:
    """流式产出 (kind, text)：kind 为 "reasoning"（思考）、"delta"（正文）
    或 "usage"（JSON 文本，仅在端点返回用量时最后一条）。

    Qwen3 等思考模型把推理放在 reasoning_content，需要单独下发，
    前端渲染为可折叠的「思考过程」块。调用指标在流结束时落库。
    """
    settings = get_settings()
    client = _client()
    t0 = time.perf_counter()
    usage: dict | None = None
    try:
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            messages=_lc_to_openai_messages(messages),
            stream=True,
            temperature=temperature,
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield "reasoning", reasoning
            if delta.content:
                yield "delta", delta.content
        metrics.record_llm(
            kind="chat",
            model=settings.llm_model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=usage["prompt_tokens"] if usage else None,
            completion_tokens=usage["completion_tokens"] if usage else None,
        )
        if usage:
            import json as _json

            yield "usage", _json.dumps(usage, ensure_ascii=False)
    except Exception:
        metrics.record_llm(
            kind="chat",
            model=settings.llm_model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            status="error",
        )
        raise
    finally:
        await client.close()


async def complete(
    messages: list[BaseMessage],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    kind: str = "eval",
) -> tuple[str, dict | None]:
    """非流式补全：返回 (正文, usage)。

    评测等后台任务默认 kind=eval；轻量判定传更小的 max_tokens 以压低延迟。
    """
    settings = get_settings()
    client = _client()
    t0 = time.perf_counter()
    kwargs: dict = {}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=_lc_to_openai_messages(messages),
            temperature=temperature,
            **kwargs,
        )
        usage = None
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        metrics.record_llm(
            kind=kind,
            model=settings.llm_model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=usage["prompt_tokens"] if usage else None,
            completion_tokens=usage["completion_tokens"] if usage else None,
        )
        text = resp.choices[0].message.content or ""
        return text, usage
    except Exception:
        metrics.record_llm(
            kind=kind,
            model=settings.llm_model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            status="error",
        )
        raise
    finally:
        await client.close()
