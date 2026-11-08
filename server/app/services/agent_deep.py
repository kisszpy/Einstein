"""智能体服务：Deep Agents harness（deepagents）。

为什么换成它：ReAct 只有"想一步做一步"，长任务没有显式计划、没有上下文
压缩、没有子智能体，48 步预算一到就只剩一句"任务没做完"。Deep Agents 把
这几件事做成了中间件：write_todos 计划、摘要压缩、task 子智能体、按路径
授权的文件系统。

和既有系统的接缝只有三处，这个文件就是那三处的实现：

1. **文件落在哪**：默认 StateBackend 把文件写进图状态（虚拟盘），前端拿不到
   产物。这里换成 FilesystemBackend，根指向 `data/projects/<ws>`，并用
   `_WorkspaceBackend` 在写入成功后回调 `ctx.note_artifact`，产物卡片才照旧。
2. **事件协议**：外部只认 tool_call / tool_result / artifact / plan / final，
   由 `_iter_events` 从 LangGraph 的 updates 流里翻译，不向上层泄漏框架细节。
3. **工具重叠**：我们的 fs_read/fs_write/fs_list 与 harness 的
   read_file/write_file 功能重复，两套并存会让模型选错，所以引擎为
   deepagents 时只保留 project_create 与业务工具，文件读写交给 harness。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.core.config import get_settings
from app.services import metrics, skill_publisher
from app.services.capabilities import ApprovalRequired, AgentContext, _artifact_kind, sandbox
from app.services.capabilities.sandbox import slugify, workspace_root
from app.services.tools import build_lc_tools, kb_directive, preflight_kb

RECURSION_LIMIT = 64   # 单份预算：有了计划工具后每步更"值钱"，比 ReAct 略宽
MAX_BUDGETS = 3        # 触顶自动续跑：把已有进展带回引擎继续执行，最多续 2 次

# harvest 自带的文件工具会顶掉我们的 fs_*，避免两套同名语义打架
SUPERSEDED_TOOLS = {"fs_read", "fs_write", "fs_list"}


class _WorkspaceBackend(FilesystemBackend):
    """工作区磁盘后端：虚拟根 = 一个工作区，写成功即记产物。

    virtual_mode=True 时 `/a/b` 映射到 `<workspace>/a/b`，`..` 与符号链接
    逃逸由父类挡掉；这里只多做一件事——把落盘成功的文件告诉 AgentContext，
    否则前端产物区、预览、下载全都拿不到路径。
    """

    def __init__(self, root: str, ctx: AgentContext) -> None:
        super().__init__(root_dir=root, virtual_mode=True)
        self.ctx = ctx

    # ---- 产物登记 ----

    def _note(self, virtual_path: str) -> None:
        """把虚拟路径换算成「项目内相对路径」——前端预览/下载都按项目拼。

        模型可能在 project_create 之前就写文件，此时 ctx.project 还是空的，
        于是退一步取路径首段当项目名（工作区里只有项目这一层目录）。
        """
        rel = (virtual_path or "").replace("\\", "/").lstrip("/")
        project = slugify(self.ctx.project or "")
        if project and rel.startswith(project + "/"):
            rel = rel[len(project) + 1:]
        elif "/" in rel:
            rel = rel.split("/", 1)[1]
        if not rel:
            return
        self.ctx.note_artifact(rel, _artifact_kind(rel))

    # ---- 项目约束 ----

    def _project_of(self, virtual_path: str) -> str | None:
        """校验写入目标是否落在已存在的项目里，返回项目名。

        harness 允许往虚拟根随手写文件，但我们的产物预览/下载是按「项目 + 相对
        路径」拼的。放任 `/随手一写.md` 会写出一个前端打不开的产物，所以在写入口
        就把项目约束补回来：第一段目录必须是已创建的项目。
        """
        rel = (virtual_path or "").replace("\\", "/").lstrip("/")
        head = rel.split("/", 1)[0] if rel else ""
        if not head:
            return None
        target = self.cwd / slugify(head)
        if not target.is_dir():
            return None
        if not self.ctx.project:
            self.ctx.project = head          # 模型自己拼对了项目名时顺势认领
        return head

    def _reject(self, virtual_path: str) -> str | None:
        if self._project_of(virtual_path) is None:
            return (
                f"路径 {virtual_path} 不在任何项目内。请先用 project_create 创建项目，"
                "再写 /项目名/文件名。"
            )
        return None

    # ---- 覆盖写操作 ----

    def write(self, file_path: str, content: str):  # noqa: ANN201 — 返回父类 WriteResult
        blocked = self._reject(file_path)
        if blocked:
            from deepagents.backends.protocol import WriteResult

            return WriteResult(error=blocked)
        result = super().write(file_path, content)
        if getattr(result, "error", None) is None:
            self._note(getattr(result, "path", None) or file_path)
        return result

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):  # noqa: ANN201
        blocked = self._reject(file_path)
        if blocked:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=blocked)
        result = super().edit(file_path, old_string, new_string, replace_all)
        if getattr(result, "error", None) is None:
            self._note(getattr(result, "path", None) or file_path)
        return result

    def upload_files(self, files):  # noqa: ANN001, ANN201
        results = super().upload_files(files)
        for item in results:
            if getattr(item, "error", None) is None:
                self._note(getattr(item, "path", ""))
        return results


DEEP_SYSTEM_PROMPT = """你是 Einstein 的工作型智能体。你能调用工具真正把活干完，但**不要什么任务都动工具**——先判断用户要的是「答案」还是「文件」，选最省力的路径。

【文件系统】
你有一个以工作区为根的文件系统：`ls` 看目录、`read_file` 读（带行号，可 offset/limit）、`write_file` 新建或整体覆盖、`edit_file` 精确替换、`glob` / `grep` 找文件内容、`delete` 删文件。路径写虚拟绝对路径，比如 `/周报/2026-W45.md`。项目目录由 `project_create` 创建，之后的文件都放在 `/项目名/...` 下。

【任务路由】
1. 问答/解释/比较/观点/规划（用户要一个回答）→ 直接回答。不建项目、不写文件。需要外部信息时先查已挂载的知识库（kb_search），查不到再 web_search；关键结论用 web_fetch 打开原文核实（max_chars 2500 左右，检索类最多 2 次），核完就答。
2. 需要精确计算/数据处理（三步以上运算、排序统计、批量转换）→ code_run 跑代码，结论以运行结果为准，算完即止。
3. 明确要文件交付（工程、可下载的 PPT/报告/文档）→ project_create 建项目，再用 write_file 落盘；代码交付必须 code_run 跑通、报错就修，再汇报。

【计划：先拆解，再执行】
1. 超过两步的任务，动手前必须先用 write_todos 把任务拆解成可执行的预设步骤：每步写清「做什么、用哪个工具、产出什么」，粒度以一次工具调用能完成为准；拆完贴出计划再逐步执行，做完一步更新一步状态。
2. 计划按任务实际需要拆步（超过 8 步也完整列出），然后自主执行到底。不要中途停下来向用户确认、也不要把任务推回给用户拆分——预算不足时系统会自动为你续接新一轮预算。
3. 执行纪律：严格按计划走，禁止偏离计划反复检索、重读同一文件；同一工具连续失败 2 次就换路径，而不是原地重试。收到「预算已续接」的系统消息时，对照计划从上次中断处继续，不要重做已完成的步骤。
4. 简单任务（一两步）不用计划工具，直接做。计划是给用户看的进度，不是交付物。

【多轮追问】
同一任务里可能带着前几轮对话。遇到「继续」「再改一下」「换成 X 风格」「详细说说第二点」时，默认指上一轮的答案或产出：在原文件上改，不要当成新任务从头再做，也不要重复已完成的工作。

【回答质量】（每一次最终回复都要做到）
1. 结论先行：第一句话直接回答问题。
2. 结构化：小标题+短列表，一句一个信息。
3. 事实与判断分开：检索来的注明来源；推断明确说是判断。
4. 不知道就直说，绝不编造数据、引用或文件内容。
5. 不复述工具调用过程，除非用户问「你怎么做的」。

【确需文件交付时】
1. 边查边写：查到一批数据立刻 write_file 落盘，不要把全部原文攒在对话里。
2. PPT 用 ppt_create：内容必须来自你的检索/计算/文件结果；关键数字用 stat、可比较数据用 chart、流程用 timeline、多观点用 grid、取舍用 vs，纯要点才用 bullets，一份里版式要有变化；theme 从 midnight/graphite/ocean/sunset/forest 里选，整份 6-10 页。
3. 纪律：不要用相同参数重复调用同一工具；拿不准就明说。

【最终回复格式】
- 第 1 类任务：直接给答案。
- 第 2/3 类任务：一句话做了什么 + 关键结论 + 产物文件清单（相对路径）。"""


def _short(obj: Any, limit: int = 300) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def _build_agent(
    ctx: AgentContext,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str,
    persona: str = "",
    temperature: float | None = None,
):
    root = workspace_root(workspace_id)
    backend = _WorkspaceBackend(str(root), ctx)
    lc_tools = [t for t in build_lc_tools(tool_ids, kb_ids, workspace_id, ctx)
                if t.name not in SUPERSEDED_TOOLS]
    # 技能：引擎原生 SkillsMiddleware。发布器已在任务开始时把 DB 技能同步到
    # <workspace>/skills/（热加载），这里只挂目录；模型按 description 自主
    # 选取，被点名的技能由 pinned_skills 状态强制装载。
    extras = f"\n\n{persona.strip()}" if persona.strip() else ""
    agent = create_deep_agent(
        model=_get_model(temperature),
        tools=lc_tools,
        # 挂了知识库就追加"KB 优先"硬规则，否则任务路由会把模型带向 web_search
        system_prompt=DEEP_SYSTEM_PROMPT + extras + (kb_directive(kb_ids) if kb_ids else ""),
        backend=backend,
        # 计划工具是这轮换引擎的主要动机，显式挂上（0.7 起默认不装）
        middleware=[TodoListMiddleware()],
        skills=[f"/{skill_publisher.SKILLS_SUBDIR}/"],
    )
    return agent


def _get_model(temperature: float | None = None):
    """复用 agent.py 的模型配置，避免两处各写一遍 base_url/timeout。"""
    from app.services.agent import _get_model as build

    return build(temperature)


def _plan_event(todos: Any) -> dict | None:
    """把 write_todos 的清单翻译成前端可渲染的计划帧。"""
    if not isinstance(todos, list) or not todos:
        return None
    items = []
    for t in todos:
        if isinstance(t, dict):
            items.append({
                "content": t.get("content") or t.get("task") or "",
                "status": t.get("status") or "pending",
            })
    return {"kind": "plan", "items": items} if items else None


def _continuation_note(last: bool) -> str:
    """预算续接时注入的用户消息：让模型从计划中断处继续，而不是重做或停机。"""
    if last:
        return (
            "（系统：步数预算已续接，这是最后一轮。请对照你的计划立即收尾："
            "完成最关键的交付，把已有成果整理成最终回复。）"
        )
    return (
        "（系统：上一轮步数预算已用完，已自动续接新一轮。"
        "请对照你的计划从上次中断处继续，不要重做已完成的步骤。）"
    )


async def _iter_events(
    agent,
    messages: list[tuple[str, str]],
    ctx: AgentContext,
    pinned_skills: list[str] | None = None,
    carry: dict | None = None,
) -> AsyncIterator[dict]:
    """把 LangGraph 的 updates 流翻成前端协议，顺便收集计划和产物。

    carry：跨预算续跑的载体。流式过程中把真实消息对象与计划状态写回
    carry，触顶后调用方用它构造续跑输入（已完成对话 + todos 不丢）。
    """
    if carry is None:
        carry = {}
    carry.setdefault("messages", [])
    carry.setdefault("todos", None)
    seen: set[str] = set()
    emitted_artifacts = 0
    last_plan = ""
    final_text = ""
    started: dict[str, float] = {}   # tool_call_id -> 开始时刻，用来算耗时

    def _plan_of(todos: Any) -> dict | None:
        nonlocal last_plan
        plan = _plan_event(todos)
        if not plan:
            return None
        stamp = json.dumps(plan["items"], ensure_ascii=False)
        if stamp == last_plan:      # 状态与工具参数会报同一份计划，去重
            return None
        last_plan = stamp
        return plan

    # pinned_skills 是 SkillsMiddleware 的状态键：被点名的技能在下一次模型
    # 调用前全文装载（用户显式指定时不赌模型自己的选择）。
    # todos 带回计划状态：续跑时模型不用重拆计划。
    invoke_input: dict[str, Any] = {"messages": messages}
    if carry.get("todos"):
        invoke_input["todos"] = carry["todos"]
    if pinned_skills:
        invoke_input["pinned_skills"] = pinned_skills
    async for update in agent.astream(
        invoke_input,
        stream_mode="updates",
        config={"recursion_limit": RECURSION_LIMIT},
    ):
        for _node, state in (update or {}).items():
            if not isinstance(state, dict):
                continue

            plan = _plan_of(state.get("todos"))
            if plan:
                yield plan
            if state.get("todos"):
                carry["todos"] = state["todos"]

            for msg in state.get("messages") or []:
                mid = getattr(msg, "id", None) or f"{type(msg).__name__}:{id(msg)}"
                if mid in seen:
                    continue
                seen.add(mid)
                carry["messages"].append(msg)

                if isinstance(msg, ToolMessage):
                    t0 = started.pop(getattr(msg, "tool_call_id", "") or "", None)
                    yield {
                        "kind": "tool_result",
                        "name": getattr(msg, "name", None) or "tool",
                        "content": _short(msg.content),
                        "duration_ms": round((time.perf_counter() - t0) * 1000) if t0 else None,
                    }
                    while emitted_artifacts < len(ctx.artifacts):
                        art = ctx.artifacts[emitted_artifacts]
                        emitted_artifacts += 1
                        yield {
                            "kind": "artifact",
                            "path": art.get("path", ""),
                            "artifact_kind": art.get("kind", "file"),
                        }
                    continue

                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        started[tc.get("id") or ""] = time.perf_counter()
                        yield {
                            "kind": "tool_call",
                            "name": tc.get("name", "tool"),
                            "args": _short(tc.get("args")),
                        }
                        if tc.get("name") == "write_todos":
                            plan = _plan_of((tc.get("args") or {}).get("todos"))
                            if plan:
                                yield plan
                    continue

                if isinstance(msg, AIMessage) and msg.content:
                    final_text = str(msg.content)

    yield {"kind": "__final_text__", "text": final_text}


async def run_deep_agent(
    task: str,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str | None = None,
    ctx: AgentContext | None = None,
    history: list[dict] | None = None,
    persona: str = "",
    pinned_skills: list[str] | None = None,
    temperature: float | None = None,
) -> AsyncIterator[dict]:
    """运行 Deep Agents，产出与 ReAct 引擎完全一致的事件流。

    persona：专家人设（附加系统提示段）；pinned_skills：被点名要强制装载
    的技能 name 清单（对应 SkillsMiddleware 的 pinned_skills 状态键）。
    """
    settings = get_settings()
    ws = workspace_id or "default"
    own_ctx = ctx is None
    ctx = ctx or AgentContext(workspace_id=ws)

    # 热加载：每个任务开始时把 DB 技能同步到工作区 skills/ 目录，
    # 增删改回滚即时生效；同时把点名的技能翻译成引擎可用的 name。
    skill_publisher.sync_skills(ws)
    pinned = [n for n in (pinned_skills or []) if n]

    agent = _build_agent(ctx, tool_ids, kb_ids, ws, persona=persona, temperature=temperature)
    messages: list[tuple[str, str]] = [
        (("assistant" if h.get("role") == "assistant" else "user"), h.get("content", ""))
        for h in (history or [])
    ]
    messages.append(("user", task))
    # 首轮强制预取检索：结果直接进上下文，不依赖模型是否记得调 kb_search
    if kb_ids and not history:
        note = await preflight_kb(kb_ids, task)
        if note:
            messages.insert(0, ("user", note))

    t0 = time.perf_counter()
    status = "ok"
    final_text = ""
    tool_calls = 0
    carry: dict[str, Any] = {"messages": [], "todos": None}
    base_messages = list(messages)  # 原始输入：续跑时用它 + carry 重建，不重复拼接
    budgets_used = 0
    try:
        while True:
            try:
                async for event in _iter_events(agent, messages, ctx, pinned_skills=pinned, carry=carry):
                    if event["kind"] == "__final_text__":
                        final_text = event["text"]
                        continue
                    if event["kind"] == "tool_call":
                        tool_calls += 1
                    yield event
                break  # 正常完成
            except GraphRecursionError:
                budgets_used += 1
                if budgets_used >= MAX_BUDGETS:
                    # 预算全部耗尽：按产品语义收尾交付，不把任务推回给用户
                    final_text = (
                        final_text
                        or f"（任务规模较大，已自动续跑 {MAX_BUDGETS} 轮仍未完成全部计划。"
                        "已完成的成果与产物见上方，本轮先交付到这里。）"
                    )
                    break
                # 自动续跑：原始输入 + 全部已发生消息 + 计划状态重新入图
                messages = [
                    *base_messages,
                    *carry["messages"],
                    ("user", _continuation_note(last=budgets_used == MAX_BUDGETS - 1)),
                ]
                yield {
                    "kind": "note",
                    "content": f"步数预算已用完，自动续接第 {budgets_used + 1}/{MAX_BUDGETS} 轮，从中断处继续…",
                }
    except ApprovalRequired as approval:
        # 敏感工具未批准：暂停任务，前端弹批准框；批准后前端自动重发任务
        yield {
            "kind": "approval_required",
            "tool": approval.tool,
            "args": _short(approval.args),
        }
        final_text = (
            f"任务已暂停：工具 `{approval.tool}` 需要你批准后才能执行。"
            "批准后我会自动继续。"
        )
        status = "paused"
    except Exception as exc:
        status = "error"
        name = type(exc).__name__
        if "Timeout" in name or "timed out" in str(exc):
            raise RuntimeError(
                "模型响应超时（任务上下文过长）。建议缩小任务范围，或分多次运行。"
            ) from exc
        raise
    finally:
        metrics.record_llm(
            kind="agent",
            model=settings.llm_model,
            duration_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=None,
            completion_tokens=None,
            status=status,
        )

    yield {
        "kind": "final",
        "text": final_text,
        "artifacts": list(ctx.artifacts) if not own_ctx else [],
        "project": ctx.project,
    }
