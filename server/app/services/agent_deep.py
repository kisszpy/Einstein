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
from app.services import metrics
from app.services.capabilities import ApprovalRequired, AgentContext, _artifact_kind, sandbox
from app.services.capabilities.sandbox import slugify, workspace_root
from app.services.tools import build_lc_tools, kb_directive, preflight_kb

RECURSION_LIMIT = 64  # 有了计划工具后每步更"值钱"，预算比 ReAct 略宽

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

【计划】
任务需要三步以上、或要跨多个文件产出时，先 write_todos 写一份计划，做完一步就更新一步；简单任务（一两步）不要用计划工具，直接做。计划是给用户看的进度，不是交付物。

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


def _build_agent(ctx: AgentContext, tool_ids: list[str], kb_ids: list[str], workspace_id: str):
    root = workspace_root(workspace_id)
    backend = _WorkspaceBackend(str(root), ctx)
    lc_tools = [t for t in build_lc_tools(tool_ids, kb_ids, workspace_id, ctx)
                if t.name not in SUPERSEDED_TOOLS]
    agent = create_deep_agent(
        model=_get_model(),
        tools=lc_tools,
        # 挂了知识库就追加"KB 优先"硬规则，否则任务路由会把模型带向 web_search
        system_prompt=DEEP_SYSTEM_PROMPT + (kb_directive(kb_ids) if kb_ids else ""),
        backend=backend,
        # 计划工具是这轮换引擎的主要动机，显式挂上（0.7 起默认不装）
        middleware=[TodoListMiddleware()],
    )
    return agent


def _get_model():
    """复用 agent.py 的模型配置，避免两处各写一遍 base_url/timeout。"""
    from app.services.agent import _get_model as build

    return build()


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


async def _iter_events(agent, messages: list[tuple[str, str]], ctx: AgentContext) -> AsyncIterator[dict]:
    """把 LangGraph 的 updates 流翻成前端协议，顺便收集计划和产物。"""
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

    async for update in agent.astream(
        {"messages": messages},
        stream_mode="updates",
        config={"recursion_limit": RECURSION_LIMIT},
    ):
        for _node, state in (update or {}).items():
            if not isinstance(state, dict):
                continue

            plan = _plan_of(state.get("todos"))
            if plan:
                yield plan

            for msg in state.get("messages") or []:
                mid = getattr(msg, "id", None) or f"{type(msg).__name__}:{id(msg)}"
                if mid in seen:
                    continue
                seen.add(mid)

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
) -> AsyncIterator[dict]:
    """运行 Deep Agents，产出与 ReAct 引擎完全一致的事件流。"""
    settings = get_settings()
    ws = workspace_id or "default"
    own_ctx = ctx is None
    ctx = ctx or AgentContext(workspace_id=ws)

    agent = _build_agent(ctx, tool_ids, kb_ids, ws)
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
    try:
        async for event in _iter_events(agent, messages, ctx):
            if event["kind"] == "__final_text__":
                final_text = event["text"]
                continue
            if event["kind"] == "tool_call":
                tool_calls += 1
            yield event
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
    except GraphRecursionError:
        final_text = (
            final_text
            or f"（已执行 {tool_calls} 次工具调用后达到步数上限，任务未能在预算内完成。"
            "建议把任务拆小或减少挂载的知识库后重试。）"
        )
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
