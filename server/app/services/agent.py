"""智能体服务：LangGraph ReAct 循环。

用 create_react_agent 预置图（agent ↔ tools 循环），astream updates 模式
产出每个节点的状态变化，映射为前端可展示的步骤事件；最终回答在
同一次运行中捕获，不重跑图。

传入 AgentContext 后自动挂载工作能力（联网/项目文件/代码/PPT），
运行中产生的文件通过 artifact 事件实时推给前端。
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
from app.services.capabilities import AgentContext
from app.services.tools import build_lc_tools

RECURSION_LIMIT = 48  # 工作型任务的步数预算（一轮工具调用 ≈ 2 步）


def _get_model() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        streaming=False,  # agent 内部按消息粒度交互，最终回答再切片下发
        timeout=300,  # 长上下文下本地 27B 生成慢，180s 会误杀
        max_retries=2,
    )


def _short(obj: Any, limit: int = 300) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


AGENT_SYSTEM_PROMPT = """你是 Einstein 的工作型智能体，能调用工具实际完成任务。但**不要什么任务都用工具**——先判断用户要的是「答案」还是「文件」，选最省力的路径：

【任务路由】
1. 问答/解释/比较/观点/规划类（用户要一个回答）→ 直接在回复里给答案。不建项目、不写代码、不产出文件。需要外部信息或时效性数据时，先 web_search、关键结论用 web_fetch 打开原文核实（max_chars 2500 左右，检索类合计最多 2 次调用），核完就答。
2. 需要精确计算/数据处理/批量文本操作（三步以上的数字运算、排序统计、大批量格式转换，心算不可靠的）→ 用 code_run 跑代码算，结论以运行结果为准。算完即止，不要为算一个数搭工程。
3. 用户明确要文件交付（代码工程、可下载的 PPT/报告/文档）→ project_create 建项目，fs_write 落盘；代码交付必须 code_run 跑通、报错就修，再汇报。

【回答质量】（每一次最终回复都要做到）
1. 结论先行：第一句话直接回答问题，依据和展开放在后面。
2. 结构化：用小标题+短列表分点，一句一个信息，不写大段流水字。
3. 事实与判断分开：检索来的内容注明来源；你自己的推断明确说是判断。
4. 不知道就直说，绝不编造数据、编造引用。
5. 不要汇报过程：除非用户问「你怎么做的」，否则回答里不要复述工具调用步骤。

【确需文件交付时】
1. 边查边写：查到一批数据立刻 fs_write 落盘成文件，不要把全部原文攒在对话里。
2. PPT 用 ppt_create：内容必须来自你的检索/计算/文件结果，不要编数据。版式为内容服务，让数据被"画出来"：关键数字用 stat 大数字页、可比较的量化数据用 chart 条形页、流程/阶段用 timeline、多观点并行用 grid、方案取舍用 vs，纯要点才用 bullets；一份里版式要有变化，不要整份都是要点页。theme 从 midnight/graphite/ocean/sunset/forest 里按主题气质选一个。整份 6-10 页。
3. 纪律：不要用相同参数重复调用任何工具；拿不准的事明确说。

【最终回复格式】
- 第 1 类任务：直接给答案（按「回答质量」要求），不汇报过程、不列文件。
- 第 2/3 类任务：一句话做了什么 + 关键结论 + 产出文件清单（相对路径）。"""


async def run_agent(
    task: str,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str | None = None,
    ctx: AgentContext | None = None,
) -> AsyncIterator[dict]:
    """运行 ReAct 智能体，产出步骤事件流。

    事件结构：
      {kind: "tool_call", name, args}
      {kind: "tool_result", name, content, duration_ms}
      {kind: "artifact", path, kind}
      {kind: "final", text, artifacts, project}
    """
    lc_tools = build_lc_tools(tool_ids, kb_ids, workspace_id, ctx)
    agent = create_react_agent(
        _get_model(),
        lc_tools,
        prompt=AGENT_SYSTEM_PROMPT,
    )
    final_text = ""
    tool_call_count = 0
    t0 = time.perf_counter()
    usage_total: dict[str, int] = {}
    pending_calls: dict[str, float] = {}  # tool_call_id -> 开始时刻

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
            config={"recursion_limit": RECURSION_LIMIT},
        ):
            for node, state in update.items():
                messages = (state or {}).get("messages", [])
                for msg in messages:
                    tool_calls = getattr(msg, "tool_calls", None)
                    if node == "agent" and tool_calls:
                        _acc_usage(msg)
                        for tc in tool_calls:
                            tool_call_count += 1
                            pending_calls[tc["id"]] = time.perf_counter()
                            yield {
                                "kind": "tool_call",
                                "name": tc["name"],
                                "args": _short(tc["args"]),
                            }
                    elif node == "tools" and getattr(msg, "content", None):
                        name = getattr(msg, "name", None) or "tool"
                        started = pending_calls.pop(getattr(msg, "tool_call_id", ""), None)
                        yield {
                            "kind": "tool_result",
                            "name": name,
                            "content": _short(msg.content),
                            "duration_ms": round((time.perf_counter() - started) * 1000) if started else None,
                        }
                        # 工具执行后把新增产物推给前端。
                        # 注意事件类型字段和产物类型字段不能重名：artifact 自带的
                        # kind（pptx/file/...）会覆盖事件 kind，路由就认不出了。
                        if ctx is not None:
                            for artifact in ctx.artifacts:
                                yield {
                                    "kind": "artifact",
                                    "path": artifact.get("path", ""),
                                    "artifact_kind": artifact.get("kind", "file"),
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
    except Exception as exc:
        status = "error"
        name = type(exc).__name__
        if "Timeout" in name or "timed out" in str(exc):
            raise RuntimeError(
                "模型响应超时（任务上下文过长）。建议：缩小任务范围，"
                "或分多次运行——先查资料写文件，再基于文件做产出。"
            ) from exc
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

    print(f"[agent] run finished status={status} calls={tool_call_count}", flush=True)
    yield {
        "kind": "final",
        "text": final_text,
        "artifacts": list(ctx.artifacts) if ctx else [],
        "project": ctx.project if ctx else "",
    }


def chunk_text(text: str, size: int = 24) -> list[str]:
    """最终回答切片。块越大 SSE 帧开销越小（4 字符一帧时冗余约 8 倍）。"""
    return [text[i : i + size] for i in range(0, len(text), size)]
