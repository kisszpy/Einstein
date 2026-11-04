"""智能体服务：LangGraph ReAct 循环。

用 create_react_agent 预置图（agent ↔ tools 循环），astream updates 模式
产出每个节点的状态变化，映射为前端可展示的步骤事件；最终回答在
同一次运行中捕获，不重跑图。
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from app.core.config import get_settings
from app.services import metrics
from app.services.tools import build_lc_tools


def _get_model() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        streaming=False,  # agent 内部按消息粒度交互，最终回答再切片下发
        timeout=120,
        max_retries=1,
    )


def _short(obj: Any, limit: int = 300) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


AGENT_SYSTEM_PROMPT = """你是一个能调用工具完成任务的智能体。纪律：
1. 检索类工具最多调用 2 次；第一次结果不理想可换一个更简洁的关键词查询重试一次。
2. 拿到工具结果后，基于结果作答——不要为了"确认"而重复调用相同工具。
3. 资料不足以完整回答时，直接基于已有信息回答，并明确说明哪部分缺少资料。
4. 回答要直接、简洁。"""


async def run_agent(
    task: str, tool_ids: list[str], kb_ids: list[str]
) -> AsyncIterator[dict]:
    """运行 ReAct 智能体，产出步骤事件流。

    事件结构：
      {kind: "tool_call", name, args}
      {kind: "tool_result", name, content}
      {kind: "final", text}  — 最后一个事件，携带完整最终回答
    """
    lc_tools = build_lc_tools(tool_ids, kb_ids)
    agent = create_react_agent(
        _get_model(),
        lc_tools,
        prompt=AGENT_SYSTEM_PROMPT,
    )
    final_text = ""
    tool_call_count = 0
    t0 = time.perf_counter()
    usage_total: dict[str, int] = {}

    def _acc_usage(msg: Any) -> None:
        token_usage = (getattr(msg, "response_metadata", None) or {}).get("token_usage")
        if token_usage:
            usage_total["prompt"] = usage_total.get("prompt", 0) + (token_usage.get("prompt_tokens") or 0)
            usage_total["completion"] = usage_total.get("completion", 0) + (token_usage.get("completion_tokens") or 0)

    status = "ok"
    try:
        async for update in agent.astream(
            {"messages": [("user", task)]},
            stream_mode="updates",
            config={"recursion_limit": 24},  # 步数预算：防止工具循环失控
        ):
            for node, state in update.items():
                messages = (state or {}).get("messages", [])
                for msg in messages:
                    tool_calls = getattr(msg, "tool_calls", None)
                    if node == "agent" and tool_calls:
                        _acc_usage(msg)
                        for tc in tool_calls:
                            tool_call_count += 1
                            yield {
                                "kind": "tool_call",
                                "name": tc["name"],
                                "args": _short(tc["args"]),
                            }
                    elif node == "tools" and getattr(msg, "content", None):
                        name = getattr(msg, "name", None) or "tool"
                        yield {
                            "kind": "tool_result",
                            "name": name,
                            "content": _short(msg.content),
                        }
                    elif node == "agent" and getattr(msg, "content", None):
                        _acc_usage(msg)
                        final_text = str(msg.content)
    except GraphRecursionError:
        # 达到步数上限：把手头已有的信息作为回答收尾，而不是报错中断
        final_text = (
            final_text
            or f"（已执行 {tool_call_count} 次工具调用后达到步数上限，"
            "任务未能在预算内完成。建议把任务拆小或减少挂载的知识库后重试。）"
        )
    except Exception:
        status = "error"
        raise
    finally:
        metrics.record_llm(
            kind="agent",
            model=get_settings().llm_model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=usage_total.get("prompt"),
            completion_tokens=usage_total.get("completion"),
            status=status,
        )

    yield {"kind": "final", "text": final_text}


def chunk_text(text: str, size: int = 4) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]
