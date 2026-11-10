"""智能体服务：默认走 Deep Agents harness，可回退到 ReAct。

两个引擎产出同一套事件（tool_call / tool_result / artifact / plan / final），
上层路由与前端无需感知差异；`APP_AGENT_ENGINE=react` 是回滚开关。

ReAct 路径（`_run_react_agent`）用 langchain 的 `create_agent` 预置图
（model ↔ tools 循环），astream updates 模式产出每个节点的状态变化，映射为
前端可展示的步骤事件；最终回答在同一次运行中捕获，不重跑图。节点名不参与
判断（只看消息类型），换 harness 的节点命名不会悄悄丢事件。
"""

import time
from collections.abc import AsyncIterator
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import models
from app.services.agent_runtime import (
    LlmTimer,
    build_messages,
    final_frame,
    memory_block,
    recall_memory,
    short,
)
from app.services.capabilities import AgentContext
from app.services.tools import build_lc_tools, kb_directive

logger = get_logger(__name__)

RECURSION_LIMIT = 48  # 工作型任务的步数预算（一轮工具调用 ≈ 2 步）


def _get_model(
    temperature: float | None = None, model_id: str = "", workspace_id: str = ""
) -> ChatOpenAI:
    """装配 LangChain 模型。

    端点来自模型注册表（`models.resolve`）：`model_id` 指定 > 平台默认 > `.env`。
    与对话侧共用同一份解析，避免"对话换了模型、智能体还在用旧的"。
    """
    settings = get_settings()
    target = models.resolve(model_id, workspace_id=workspace_id)
    kwargs: dict = dict(
        base_url=target.base_url,
        api_key=target.api_key,
        model=target.model,
        # 真流式的开关：astream(messages) 靠模型的 SSE 增量，streaming=False
        # 时它只在最后吐一个完整 chunk，前端就得等全文生成完（旧行为）。
        streaming=bool(getattr(settings, "agent_stream", True)),
        timeout=300,  # 长上下文下本地 27B 生成慢，180s 会误杀
        max_retries=2,
    )
    resolved_temp = temperature if temperature is not None else target.temperature
    if resolved_temp is not None:
        kwargs["temperature"] = resolved_temp
    return ChatOpenAI(**kwargs)


AGENT_SYSTEM_PROMPT = """你是 Einstein 的工作型智能体，能调用工具实际完成任务。但**不要什么任务都用工具**——先判断用户要的是「答案」还是「文件」，选最省力的路径：

【任务路由】
1. 问答/解释/比较/观点/规划类（用户要一个回答）→ 直接在回复里给答案。不建项目、不写代码、不产出文件。需要外部信息或时效性数据时，先查已挂载的知识库（kb_search），查不到再 web_search；关键结论用 web_fetch 打开原文核实（max_chars 2500 左右，检索类合计最多 2 次调用），核完就答。
2. 需要精确计算/数据处理/批量文本操作（三步以上的数字运算、排序统计、大批量格式转换，心算不可靠的）→ 用 code_run 跑代码算（不需要先建项目，无项目时自动落在临时沙盒），结论以运行结果为准。算完即止，不要为算一个数建工程。
3. 用户明确要文件交付（代码工程、可下载的 PPT/报告/文档）→ project_create 建项目，fs_write 落盘；代码交付必须 code_run 跑通、报错就修，再汇报。

【多轮追问】
同一个任务里可能带着前几轮对话。遇到「继续」「再改一下」「换成 X 风格」「详细说说第二点」这类追问时，默认指上一轮的答案或产出：在原有基础上改，不要当成新任务从头再做，也不要重复已经完成的工作。

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
    history: list[dict] | None = None,
    persona: str = "",
    pinned_skills: list[str] | None = None,
    temperature: float | None = None,
    thread_id: str = "",
    resume: dict | None = None,
) -> AsyncIterator[dict]:
    """按配置选择引擎，对外只有这一个入口。

    persona/pinned_skills/temperature 仅 deep 引擎支持（技能是 deepagents
    原生能力），ReAct 引擎自动忽略。

    thread_id/resume：人工确认的恢复入口。deep 系引擎据此从检查点续跑；
    ReAct 引擎没有检查点，忽略这两个参数（批准后按批准重跑）。

    events：
      {kind: "tool_call", name, args}
      {kind: "tool_result", name, content, duration_ms}
      {kind: "plan", items: [{content, status}]}
      {kind: "artifact", path, artifact_kind}
      {kind: "final", text, artifacts, project}
    """
    engine = (get_settings().agent_engine or "").strip().lower()
    if engine == "react":
        async for step in _run_react_agent(task, tool_ids, kb_ids, workspace_id, ctx, history):
            yield step
        return

    if engine in ("deepagents-pro", "deep-pro", "pro"):
        # Pro 版：在 deep 之上多给真沙箱（execute）与浏览器，事件协议不变
        from app.services.agent_deep_pro import run_deep_pro_agent

        async for step in run_deep_pro_agent(
            task, tool_ids, kb_ids, workspace_id, ctx, history,
            persona=persona, pinned_skills=pinned_skills, temperature=temperature,
            thread_id=thread_id, resume=resume,
        ):
            yield step
        return

    from app.services.agent_deep import run_deep_agent  # 延迟导入：换引擎不该拖慢启动

    async for step in run_deep_agent(
        task, tool_ids, kb_ids, workspace_id, ctx, history,
        persona=persona, pinned_skills=pinned_skills, temperature=temperature,
        thread_id=thread_id, resume=resume,
    ):
        yield step


async def _run_react_agent(
    task: str,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str | None = None,
    ctx: AgentContext | None = None,
    history: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """ReAct 引擎（回滚用）：单循环、无计划、无子智能体。"""
    lc_tools = build_lc_tools(tool_ids, kb_ids, workspace_id, ctx)
    # 长期记忆与 deep 引擎同一套：回滚开关只换执行引擎，不该换掉模型的背景知识
    memories = recall_memory(ctx, task) if ctx is not None else []
    agent = create_agent(
        _get_model(
            model_id=getattr(ctx, "model_id", "") if ctx else "",
            workspace_id=getattr(ctx, "workspace_id", "") if ctx else "",
        ),
        lc_tools,
        # 挂了知识库就追加"KB 优先"硬规则，与 deepagents 引擎保持一致
        system_prompt=(
            AGENT_SYSTEM_PROMPT
            + memory_block(memories)
            + (kb_directive(kb_ids) if kb_ids else "")
        ),
    )
    final_text = ""
    tool_call_count = 0
    pending_calls: dict[str, float] = {}  # tool_call_id -> 开始时刻
    seen: set[str] = set()                # 节点可能回放历史消息，按 id 去重
    messages = await build_messages(task, history, kb_ids)

    _timed_model = models.resolve(
        getattr(ctx, "model_id", "") if ctx else "",
        workspace_id=getattr(ctx, "workspace_id", "") if ctx else "",
    ).model
    with LlmTimer("agent", _timed_model) as timer:
        def _acc_usage(msg: Any) -> None:
            token_usage = (getattr(msg, "response_metadata", None) or {}).get("token_usage")
            if token_usage:
                timer.tokens(token_usage.get("prompt_tokens"), token_usage.get("completion_tokens"))

        try:
            async for update in agent.astream(
                {"messages": messages},
                stream_mode="updates",
                config={"recursion_limit": RECURSION_LIMIT},
            ):
                for _node, state in update.items():
                    messages = (state or {}).get("messages", [])
                    for msg in messages:
                        mid = getattr(msg, "id", None) or f"{type(msg).__name__}:{id(msg)}"
                        if mid in seen:
                            continue
                        seen.add(mid)
                        if isinstance(msg, ToolMessage):
                            name = getattr(msg, "name", None) or "tool"
                            started = pending_calls.pop(getattr(msg, "tool_call_id", ""), None)
                            yield {
                                "kind": "tool_result",
                                "name": name,
                                "content": short(msg.content),
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
                            continue

                        if not isinstance(msg, AIMessage):
                            continue
                        _acc_usage(msg)
                        tool_calls = getattr(msg, "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                tool_call_count += 1
                                pending_calls[tc["id"]] = time.perf_counter()
                                yield {
                                    "kind": "tool_call",
                                    "name": tc["name"],
                                    "args": short(tc["args"]),
                                }
                        elif msg.content:
                            final_text = str(msg.content)
        except GraphRecursionError:
            # 达到步数上限：把手头已有的信息作为回答收尾，而不是报错中断
            final_text = (
                final_text
                or f"（已执行 {tool_call_count} 次工具调用后达到步数上限，"
                "任务未能在预算内完成。建议把任务拆小或减少挂载的知识库后重试。）"
            )
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name or "timed out" in str(exc):
                raise RuntimeError(
                    "模型响应超时（任务上下文过长）。建议：缩小任务范围，"
                    "或分多次运行——先查资料写文件，再基于文件做产出。"
                ) from exc
            raise

    logger.info("运行结束 status=%s calls=%d", timer.status, tool_call_count)
    yield final_frame(ctx, final_text, with_artifacts=ctx is not None)


def chunk_text(text: str, size: int = 24) -> list[str]:
    """最终回答切片。块越大 SSE 帧开销越小（4 字符一帧时冗余约 8 倍）。"""
    return [text[i : i + size] for i in range(0, len(text), size)]
