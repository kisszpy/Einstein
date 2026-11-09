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
from app.services import metrics, models, ratelimit, traces


def get_chat_model(model_id: str = "", temperature: float | None = None) -> ChatOpenAI:
    """构建 LangChain 聊天模型。

    `model_id` 走注册表（`models.resolve`），空则用默认模型、再退 `.env`。
    LangChain 路径（智能体）与原生流式路径共用同一份解析逻辑，避免
    「对话用了新模型、智能体还在用旧模型」这种漂移。
    """
    settings = get_settings()
    target = models.resolve(model_id)
    kwargs: dict = dict(
        base_url=target.base_url,
        api_key=target.api_key,
        model=target.model,
        streaming=True,
        timeout=120,
        max_retries=1,
    )
    resolved_temp = temperature if temperature is not None else target.temperature
    if resolved_temp is not None:
        kwargs["temperature"] = resolved_temp
    return ChatOpenAI(**kwargs)



def _lc_to_openai_messages(messages: list[BaseMessage]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = {"human": "user", "ai": "assistant", "system": "system"}.get(
            m.type, "user"
        )
        out.append({"role": role, "content": str(m.content)})
    return out


def _client(target: models.Resolved | None = None) -> AsyncOpenAI:
    resolved = target or models.resolve()
    return AsyncOpenAI(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        timeout=180,
        max_retries=1,
    )


def _endpoints(model_id: str = "") -> list[tuple[str, str, str]]:
    """主端点在前、降级链在后：[(base_url, api_key, model)]。

    主端点来自模型注册表（`model_id` 指定，或默认模型，或 `.env`）；
    fallback 格式 `base_url::model::api_key`，分号分隔多条，解析失败的条目跳过。
    降级链刻意不查注册表：它是「主端点整体不可用」时的兜底，用配置里写死的
    静态列表更可预期，也不会因为某条模型被停用而整条链消失。
    """
    settings = get_settings()
    head = models.resolve(model_id)
    out = [(head.base_url, head.api_key, head.model)]
    for raw in (settings.llm_fallbacks or "").split(";"):
        parts = [p.strip() for p in raw.split("::") if p.strip()]
        if len(parts) >= 2:
            out.append((parts[0], parts[2] if len(parts) > 2 else head.api_key, parts[1]))
    return out


async def stream_chat(
    messages: list[BaseMessage],
    temperature: float = 0.7,
    model_id: str = "",
) -> AsyncIterator[tuple[str, str]]:
    """流式产出 (kind, text)：kind 为 "reasoning"（思考）、"delta"（正文）
    或 "usage"（JSON 文本，仅在端点返回用量时最后一条）。

    Qwen3 等思考模型把推理放在 reasoning_content，需要单独下发，
    前端渲染为可折叠的「思考过程」块。调用指标在流结束时落库。
    """
    target = models.resolve(model_id)
    t0 = time.perf_counter()
    usage: dict | None = None
    try:
        stream = None
        # 降级链：连接阶段失败（端点宕机/超时）依次试下一个；流开始后不再切换
        last_exc: Exception | None = None
        for base_url, api_key, model in _endpoints(model_id):
            client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=180, max_retries=1)
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=_lc_to_openai_messages(messages),
                    stream=True,
                    temperature=temperature,
                    stream_options={"include_usage": True},
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        if stream is None:
            raise last_exc  # type: ignore[misc]
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
            model=target.model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=usage["prompt_tokens"] if usage else None,
            completion_tokens=usage["completion_tokens"] if usage else None,
        )
        if usage:
            import json as _json

            traces.note_tokens(usage["prompt_tokens"], usage["completion_tokens"])
            ratelimit.record_tokens(
                usage["prompt_tokens"] + usage["completion_tokens"]
            )
            yield "usage", _json.dumps(usage, ensure_ascii=False)
    except Exception:
        metrics.record_llm(
            kind="chat",
            model=target.model,
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
    model_id: str = "",
) -> tuple[str, dict | None]:
    """非流式补全：返回 (正文, usage)。

    评测等后台任务默认 kind=eval；轻量判定传更小的 max_tokens 以压低延迟。
    """
    target = models.resolve(model_id)
    client = _client(target)
    t0 = time.perf_counter()
    kwargs: dict = {}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    try:
        resp = await client.chat.completions.create(
            model=target.model,
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
            traces.note_tokens(usage["prompt_tokens"], usage["completion_tokens"])
            ratelimit.record_tokens(
                usage["prompt_tokens"] + usage["completion_tokens"]
            )
        metrics.record_llm(
            kind=kind,
            model=target.model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=usage["prompt_tokens"] if usage else None,
            completion_tokens=usage["completion_tokens"] if usage else None,
        )
        text = resp.choices[0].message.content or ""
        return text, usage
    except Exception:
        metrics.record_llm(
            kind=kind,
            model=target.model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            status="error",
        )
        raise
    finally:
        await client.close()
