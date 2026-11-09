"""智能体服务：Deep Agents harness（deepagents）。

为什么用它：ReAct 只有"想一步做一步"，长任务没有显式计划、没有上下文
压缩、没有子智能体，步数预算一到就只剩一句"任务没做完"。Deep Agents 把
这几件事做成了中间件：write_todos 计划、摘要压缩、task 子智能体、按路径
授权的文件系统。

**这个文件只做框架没有的三件事**，其余全部交给 harness：

1. **文件落在哪**：默认 StateBackend 把文件写进图状态（虚拟盘），前端拿不到
   产物。这里换成 FilesystemBackend，根指向 `data/projects/<ws>`，并用
   `WorkspaceBackend` 在写入成功后回调 `ctx.note_artifact`，产物卡片才照旧。
2. **事件协议**：外部只认 tool_call / tool_result / artifact / plan / final，
   由 `_iter_events` 从 LangGraph 的 updates 流里翻译。
3. **预算续跑**：产品要求"触顶自动续接而不是把任务推回用户"，harness 只有
   一个 `recursion_limit`（默认 9999，见 graph.py:1028），没有续跑语义，所以
   循环写在 `_run_engine` 里；三个 deep 变体共用它。

工具重叠：我们的 fs_read/fs_write/fs_list 与 harness 的 read_file/write_file
功能重复，两套并存会让模型选错，所以引擎为 deepagents 时只保留
project_create 与业务工具（SUPERSEDED_TOOLS），文件读写交给 harness。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.middleware.filesystem import FilesystemMiddleware, supports_execution
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.core.config import get_settings
from app.services import checkpoints, skill_publisher
from app.services.agent_runtime import (
    LlmTimer,
    build_messages,
    final_frame,
    get_checkpointer,
    memory_block,
    recall_memory,
    short,
    thread_config,
)
from app.services.approvals import args_fingerprint
from app.services.capabilities import ApprovalRequired, AgentContext, _artifact_kind
from app.services.capabilities.sandbox import slugify, workspace_root
from app.services.tools import build_lc_tools, kb_directive

RECURSION_LIMIT = 64   # 单份预算：有了计划工具后每步更"值钱"，比 ReAct 略宽
MAX_BUDGETS = 3        # 触顶自动续跑：把已有进展带回引擎继续执行，最多续 2 次

# harvest 自带的文件工具会顶掉我们的 fs_*，避免两套同名语义打架
SUPERSEDED_TOOLS = {"fs_read", "fs_write", "fs_list"}

# 框架文件工具里与项目边界无关的那部分。`execute` 不在其中——它只在后端
# 真的能执行命令时才挂（见 _fs_tool_names）。
FS_TOOL_NAMES = ("ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep")


def _fs_tool_names(backend: Any) -> list[str]:
    """按后端真实能力决定挂哪些文件工具。

    框架默认把 `execute` 一并注册（`middleware/filesystem.py:2073`），但它
    只做运行期检查——后端不支持时返回一句英文错误。模型看到"能跑命令"就会
    先试一次，白跑一轮、计划被打断。这里在装配期就按能力裁掉，做到
    「工具集 ⊆ 后端能力集」；Pro 引擎的后端支持执行，`execute` 照常挂上。
    """
    names = list(FS_TOOL_NAMES)
    if supports_execution(backend):
        names.append("execute")
    return names


class WorkspaceBackend(FilesystemBackend):
    """工作区磁盘后端：虚拟根 = 一个工作区，写成功即记产物。

    virtual_mode=True 时 `/a/b` 映射到 `<workspace>/a/b`，`..` 与符号链接
    逃逸由父类挡掉。这里额外做两件事：

    1. **项目边界（防穿项目）**：本次任务只能访问「活动项目」（用户挂载的
       项目，或运行中 project_create 创建的项目）与 `/.agent/skills/` 技能目录
       （只读，供技能渐进披露）。工作区其他项目读/写/搜索/删除全部拒绝。
    2. **产物登记**：落盘成功的文件告诉 AgentContext，前端产物区、预览、
       下载才照旧可用。

    **为什么不用声明式 `FilesystemPermission`**：它是构建期快照（中间件在
    `create_deep_agent` 时拿一份 list 拷贝，见 middleware/filesystem.py:2087），
    而这里的"允许范围"是运行期变量——project_create 会中途改写 ctx.project；
    另外 execute-capable 后端与权限列表互斥（同文件 2089 行直接抛
    NotImplementedError）。语义上只有后端能看到每一次调用，所以边界只能落
    在这一层；静态部分（技能目录只读、运行时目录禁止写入）也一并放这里，
    避免两套规则互相打架。
    """

    def __init__(self, root: str, ctx: AgentContext) -> None:
        super().__init__(root_dir=root, virtual_mode=True)
        self.ctx = ctx

    # ---- 项目边界 ----

    def _allowed(self) -> str:
        """本次任务允许访问的项目 slug（动态读取：project_create 创建后即生效）。"""
        return slugify(self.ctx.project) if self.ctx.project else ""

    @staticmethod
    def _rel(virtual_path: str) -> str:
        return (virtual_path or "").replace("\\", "/").strip("/")

    def _head(self, virtual_path: str) -> str:
        """虚拟路径首段（项目名/特殊目录名）。"""
        rel = self._rel(virtual_path)
        return rel.split("/", 1)[0] if rel else ""

    def _in_skills(self, virtual_path: str) -> bool:
        """是否落在运行时的技能目录里。

        只认目录本身与它的下面，不认上级——根目录 ls 因此不会列出 `.agent`：
        模型看到的工作区根目录里只有它自己的项目。
        """
        rel = self._rel(virtual_path)
        sub = skill_publisher.SKILLS_SUBDIR
        if not rel:
            return False
        return rel == sub or rel.startswith(sub + "/")

    def _in_scope(self, virtual_path: str) -> bool:
        """读取范围：活动项目内 或 /.agent/skills/（技能渐进披露要读 SKILL.md）。"""
        if self._in_skills(virtual_path):
            return True
        head = self._head(virtual_path)
        if not head:
            return True
        allowed = self._allowed()
        return bool(allowed) and slugify(head) == allowed

    def _read_blocked(self, virtual_path: str) -> str | None:
        if self._in_scope(virtual_path):
            return None
        allowed = self._allowed()
        if not allowed:
            return (
                f"路径 {virtual_path} 超出本次任务范围：当前没有活动项目，只能读取 "
                f"/{skill_publisher.SKILLS_SUBDIR}/ 下的技能文件。请先用 project_create "
                "创建项目，或让用户在界面上挂载项目后再访问项目文件。"
            )
        return (
            f"路径 {virtual_path} 不在本次任务的项目（{allowed}）内。"
            "为避免串到别的项目，已拒绝访问。"
        )

    def _write_blocked(self, virtual_path: str) -> str | None:
        rel = self._rel(virtual_path)
        if rel == ".agent" or rel.startswith(".agent/"):
            return "/.agent/ 是平台运行时目录（技能等），只读，禁止写入。"
        head = self._head(virtual_path)
        allowed = self._allowed()
        if not allowed:
            return (
                f"路径 {virtual_path} 不在任务范围内：请先用 project_create 创建项目，"
                "再写 /项目名/文件名。"
            )
        if slugify(head) != allowed:
            return (
                f"路径 {virtual_path} 不在本次任务的项目（{allowed}）内。"
                "为避免串到别的项目，已拒绝写入。"
            )
        return None

    def _scope_search_path(self, path: str | None) -> tuple[str | None, str | None]:
        """把根级搜索收敛到活动项目内；越界路径直接拒绝。

        返回 (收敛后的搜索路径或 None, 错误消息或 None)。
        """
        # 注意 path 为空时 _in_scope 会返回 True（根目录本身允许 ls），
        # 但根级搜索必须收敛到活动项目，不能顺手把整个工作区扫了
        if path and (self._in_skills(path) or self._in_scope(path)):
            return path, None
        head = self._head(path or "")
        allowed = self._allowed()
        if not head and allowed:
            return f"/{allowed}", None  # 根级搜索限定到活动项目
        if head:
            return None, self._read_blocked(path or "")
        return None, (
            "当前没有活动项目，无法从根目录搜索。请先用 project_create 创建项目，"
            "或在指定项目路径下搜索。"
        )

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

    # ---- 覆盖写操作：先过项目边界，再登记产物 ----

    def write(self, file_path: str, content: str):  # noqa: ANN201 — 返回父类 WriteResult
        blocked = self._write_blocked(file_path)
        if blocked:
            return WriteResult(error=blocked)
        result = super().write(file_path, content)
        if getattr(result, "error", None) is None:
            self._note(getattr(result, "path", None) or file_path)
        return result

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):  # noqa: ANN201
        blocked = self._write_blocked(file_path)
        if blocked:
            return EditResult(error=blocked)
        result = super().edit(file_path, old_string, new_string, replace_all)
        if getattr(result, "error", None) is None:
            self._note(getattr(result, "path", None) or file_path)
        return result

    def delete(self, file_path: str):  # noqa: ANN201
        blocked = self._write_blocked(file_path)
        if blocked:
            return DeleteResult(error=blocked)
        return super().delete(file_path)

    def upload_files(self, files):  # noqa: ANN001, ANN201
        results = super().upload_files(files)
        for item in results:
            if getattr(item, "error", None) is not None:
                continue
            blocked = self._write_blocked(getattr(item, "path", ""))
            if blocked:
                item.error = blocked
            else:
                self._note(getattr(item, "path", ""))
        return results

    # ---- 覆盖读操作：ls 过滤越界目录，read 直接拒绝 ----

    def ls(self, path: str):  # noqa: ANN201
        result: LsResult = super().ls(path)
        if result.error is None and result.entries is not None:
            # 根目录：只显示活动项目与技能目录，其他项目对模型不可见
            result.entries = [e for e in result.entries if self._in_scope(e.get("path", ""))]
        return result

    def read(self, file_path: str, offset: int = 0, limit: int = 2000):  # noqa: ANN201
        blocked = self._read_blocked(file_path)
        if blocked:
            return ReadResult(error=blocked)
        return super().read(file_path, offset, limit)

    def glob(self, pattern: str, path: str | None = None):  # noqa: ANN201
        scoped, err = self._scope_search_path(path)
        if err:
            return GlobResult(error=err)
        return super().glob(pattern, scoped)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None, context_lines: int = 0):  # noqa: ANN201
        scoped, err = self._scope_search_path(path)
        if err:
            return GrepResult(error=err)
        return super().grep(pattern, scoped, glob, max_count=max_count, context_lines=context_lines)


DEEP_SYSTEM_PROMPT = """你是 Einstein 的工作型智能体。你能调用工具真正把活干完，但**不要什么任务都动工具**——先判断用户要的是「答案」还是「文件」，选最省力的路径。

【文件系统】
你有一个以工作区为根的文件系统：`ls` 看目录、`read_file` 读（带行号，可 offset/limit）、`write_file` 新建或整体覆盖、`edit_file` 精确替换、`glob` / `grep` 找文件内容、`delete` 删文件。路径写虚拟绝对路径，比如 `/周报/2026-W45.md`。项目目录由 `project_create` 创建，之后的文件都放在 `/项目名/...` 下。

【项目边界】
你只能访问本次任务的活动项目（用户挂载的项目，或你本次用 project_create 创建的项目）与 `/.agent/skills/` 技能目录（只读，平台运行时目录，不要写）。工作区里的其他项目对你不可见也不可写，访问会被拒绝——不要尝试绕过。glob/grep 从根搜索时系统会自动限定到活动项目内。若用户已挂载项目，直接在该项目内读和产出，不要再 project_create 新建项目。

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
2. PPT 标准路线是 slidep DSL 逐页闭环（视觉效果远好于其他方式）：① 每页用 fs_write 写一个 slides/NN.slide 文件，内容是 JSX 风格 DSL——`<Slide width={1280} height={720} background="…">` 里用 `<Box style={{...}}>` 做布局容器（支持 Flexbox：display:"flex"、flexDirection、gap、padding、borderRadius、background，background 支持 linear-gradient 渐变与 rgba 玻璃卡）和 `<Text style={{fontSize,color,fontWeight,marginTop}}>` 写文字；② 调 slide_upsert(pptx_path="deck/xxx.pptx", dsl_file="slides/NN.slide") 编译插入；③ 每页 upsert 后必须 slide_lint，diagnostics 有 error 就改 DSL 后以相同 page_index 重新 upsert，直到无 error 再下一页。slidep 连续失败 3 次才降级用 ppt_create。
3. slidep 设计规范：画布 1280×720、四周安全区 ≥60px；页面主标题 ≥32px、卡片小标题 ≥22px、正文 18-20px、图表标签 ≥16px、脚注 ≥14px；同页样式组合 ≤4 种；块间距只用 24/32 两档；卡片高度贴合内容不空撑；一页分区 ≤4 块且有唯一视觉主角；禁止等宽均分构图、禁止空容器、禁止 emoji 和感叹号；标题不用「卓越表现/行业领先」这类空话，用具体数字与动词；页数按用户要求来，没有硬性上限。
4. 设计系统纪律：动笔前先选定一个设计家族（深色编辑风/浅色商务风/渐变科技风/暖调杂志风，详见 slidep 技能），全篇统一。用户要求重画、重新生成、换风格时，必须换一个与上一版不同的设计家族（新配色+新母题），严禁沿用旧观感微调。封面与章节页用渐变背景和玻璃卡片提升质感，内容页用纯色底。
5. ppt_create 仅作降级备选；Word/Excel 优先用对应内置工具，不要手写 python-docx/openpyxl 脚本替代。
6. 纪律：不要用相同参数重复调用同一工具；拿不准就明说。

【最终回复格式】
- 第 1 类任务：直接给答案。
- 第 2/3 类任务：一句话做了什么 + 关键结论 + 产物文件清单（相对路径）。"""


def _build_agent(
    ctx: AgentContext,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str,
    persona: str = "",
    temperature: float | None = None,
    *,
    backend: Any = None,
    prompt_extra: str = "",
    extra_tools: Sequence[Any] = (),
    interrupt_on: dict[str, Any] | None = None,
    checkpointer: Any = None,
):
    """装配一个 deep agent。后端与额外工具可注入（Pro 版用带 execute 的后端）。

    技能：引擎原生 SkillsMiddleware。发布器已在任务开始时把 DB 技能同步到
    <workspace>/.agent/skills/（热加载），这里只挂目录；模型按 description
    自主选取，被点名的技能由 pinned_skills 状态强制装载。

    人工确认：走框架的 `interrupt_on` + checkpointer（见 `_approval_plan`）。
    引擎自己不再发明"抛出异常当暂停"的协议——那样批准后只能重跑。
    """
    if backend is None:
        backend = WorkspaceBackend(str(workspace_root(workspace_id)), ctx)

    lc_tools = [t for t in build_lc_tools(tool_ids, kb_ids, workspace_id, ctx)
                if t.name not in SUPERSEDED_TOOLS]
    lc_tools.extend(extra_tools)
    extras = f"\n\n{persona.strip()}" if persona.strip() else ""
    return create_deep_agent(
        model=_get_model(
            temperature, getattr(ctx, "model_id", ""), getattr(ctx, "workspace_id", "")
        ),
        tools=lc_tools,
        # 挂了知识库就追加"KB 优先"硬规则，否则任务路由会把模型带向 web_search
        system_prompt=(
            DEEP_SYSTEM_PROMPT + prompt_extra + extras
            + memory_block(list(getattr(ctx, "memory", []) or []))
            + (kb_directive(kb_ids) if kb_ids else "")
        ),
        backend=backend,
        # 计划工具是这轮换引擎的主要动机，显式挂上（0.7 起默认不装）
        middleware=[
            TodoListMiddleware(),
            # 同名中间件会替换框架内置的那个（deepagents graph.py:236）。
            # 这是唯一能在装配期裁掉 execute 的接缝；代价是丢掉 harness 的
            # 工具描述覆盖，可接受——描述我们自己在提示词里写清了。
            FilesystemMiddleware(backend=backend, tools=_fs_tool_names(backend)),
        ],
        skills=[f"/{skill_publisher.SKILLS_SUBDIR}/"],
        interrupt_on=interrupt_on,
        # 中断必须有检查点才能续跑。调用方（_run_deep）会先把持久化 saver 传进来；
        # 没传时退回内存版——测试和脚本直接调这个函数，不该被异步初始化拖住。
        checkpointer=(checkpointer or get_checkpointer()) if interrupt_on else None,
    )


def _get_model(temperature: float | None = None, model_id: str = "", workspace_id: str = ""):
    """复用 agent.py 的模型配置，避免两处各写一遍 base_url/timeout。"""
    from app.services.agent import _get_model as build

    return build(temperature, model_id, workspace_id)


def _approval_tools() -> set[str]:
    """需要人工确认的工具名（配置里逗号分隔）。空集 = 关闭审批。"""
    raw = get_settings().agent_approval_tools or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def _approval_plan(ctx: AgentContext) -> dict[str, Any] | None:
    """需要人工确认的工具 → `interrupt_on` 配置；空表示不启用。

    同时把 `ctx.hitl` 置真：工具层因此不再自己抛 ApprovalRequired——拦截发生
    在工具执行之前，工具代码保持"被调用就是已批准"的干净语义。
    """
    tools = _approval_tools()
    if not tools:
        return None
    ctx.hitl = True
    return {name: True for name in tools}


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


def _chunk_delta(chunk: Any, meta: Any) -> str:
    """从 messages 流的一个 chunk 里取出「可以立刻显示给用户」的文本增量。

    两道过滤，缺一个就会出现"答案里混进别人的话"：
    1. **子图/子智能体**：`task` 委派出去的 agent 用的是同一个模型，它边想边说
       的文本也在 messages 流里。父子图在 checkpoint_ns 里用 `|` 连接
       （`model:uuid|model:uuid`），主图的 ns 只有一段（`model:uuid`），
       所以含 `|` 的一律丢掉。**注意不能把 `:` 当分隔符**——主图自己的 ns
       就长这样，那样会把主模型的输出全过滤掉（实测踩过）。
    2. **非助手增量**：只有 `AIMessageChunk` 才有正文；工具结果、状态更新都在
       updates 流里，不在这里。
    """
    if not isinstance(chunk, AIMessageChunk):
        return ""
    ns = str((meta or {}).get("langgraph_checkpoint_ns") or "")
    if "|" in ns:
        return ""
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 部分网关把正文拆成 [{type:"text", text:"..."}]
        return "".join(
            part.get("text") or ""
            for part in content
            if isinstance(part, dict) and str(part.get("type", "")).startswith("text")
        )
    return ""


async def _iter_events(
    agent,
    payload: Any,
    ctx: AgentContext,
    pinned_skills: list[str] | None = None,
    carry: dict | None = None,
    thread_id: str = "",
) -> AsyncIterator[dict]:
    """把 LangGraph 的 updates 流翻成前端协议，顺便收集计划和产物。

    payload：首次输入（`{"messages": [...]}`）或续跑命令（`Command(resume=...)`）。
    carry：跨预算续跑的载体。流式过程中把真实消息对象与计划状态写回
    carry，触顶后调用方用它构造续跑输入（已完成对话 + todos 不丢）。
    thread_id：检查点线程。有它才能中断后续跑，也让 todos 跨请求存活。

    真流式：同时订阅 `messages` 模式，把模型增量作为 `delta` 事件立刻交给
    上层。工具调用轮次里模型也可能先说话，一旦开始调工具就发 `answer_reset`
    让前端把这段"过程中的话"撤掉——否则最终答案里会掺进两轮草稿。
    """
    if carry is None:
        carry = {}
    carry.setdefault("messages", [])
    carry.setdefault("todos", None)
    seen: set[str] = set()
    emitted_artifacts = 0
    last_plan = ""
    final_text = ""
    answer_buf = ""                  # reset 之后已下发给前端的正文
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
    # todos 带回计划状态：续跑时模型不用重拆计划（有检查点时它本来就在图状态里）。
    if isinstance(payload, dict):
        invoke_input: Any = dict(payload)
        if carry.get("todos"):
            invoke_input["todos"] = carry["todos"]
        if pinned_skills:
            invoke_input["pinned_skills"] = pinned_skills
    else:
        invoke_input = payload      # Command(resume=...) 原样送入

    streaming = bool(getattr(get_settings(), "agent_stream", True))
    stream_mode: Any = ["updates", "messages"] if streaming else "updates"

    async for item in agent.astream(
        invoke_input,
        stream_mode=stream_mode,
        config=thread_config(thread_id, RECURSION_LIMIT),
    ):
        # 多模式订阅时事件是 (mode, payload)；单模式时就是 payload 本身
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
            mode, data = item
        else:
            mode, data = "updates", item

        if mode == "messages":
            chunk, meta = data if isinstance(data, tuple) else (data, {})
            text = _chunk_delta(chunk, meta)
            if text:
                answer_buf += text
                yield {"kind": "delta", "text": text}
            continue

        update = data
        interrupted: Any = None
        for _node, state in (update or {}).items():
            # 中断是「图暂停」而不是错误：LangGraph 用 __interrupt__ 键把
            # 待确认的动作交出来，这里转成 approval_required 事件，结束本轮。
            if _node == "__interrupt__":
                interrupted = state
                continue
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
                        "content": short(msg.content),
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
                    if answer_buf:
                        # 这一轮的正文只是"调工具之前说的话"，不是最终答案
                        answer_buf = ""
                        yield {"kind": "answer_reset"}
                    for tc in tool_calls:
                        started[tc.get("id") or ""] = time.perf_counter()
                        yield {
                            "kind": "tool_call",
                            "name": tc.get("name", "tool"),
                            "args": short(tc.get("args")),
                        }
                        if tc.get("name") == "write_todos":
                            plan = _plan_of((tc.get("args") or {}).get("todos"))
                            if plan:
                                yield plan
                    continue

                if isinstance(msg, AIMessage) and msg.content:
                    final_text = str(msg.content)

        if interrupted is not None:
            event = _interrupt_event(interrupted)
            if event:
                yield {"kind": "__interrupt__", "value": event}
            return

    yield {"kind": "__final_text__", "text": final_text, "streamed": answer_buf}


def _interrupt_event(interrupted: Any) -> dict | None:
    """把 LangGraph 的 interrupt 载荷翻成前端要的暂停事件。

    HITL 批量模式下的载荷形如
    `{"action_requests": [{"name": "code_run", "args": {...}, "description": ...}]}`。
    这里只取第一个动作做展示，`args_hash` 交给前端原样回传——批准只对**这一组
    参数**有效，参数变了要重新确认（见 services/approvals.py）。
    """
    items = interrupted if isinstance(interrupted, (list, tuple)) else [interrupted]
    for item in items:
        value = getattr(item, "value", item)
        if not isinstance(value, dict):
            continue
        actions = value.get("action_requests") or []
        first = actions[0] if actions and isinstance(actions[0], dict) else {}
        tool = str(first.get("name") or "")
        args = first.get("args") or {}
        if not tool:
            configs = value.get("review_configs") or []
            if configs and isinstance(configs[0], dict):
                tool = str(configs[0].get("action_name") or "")
        return {
            "tool": tool,
            "args": short(args),
            "args_hash": args_fingerprint(args),
            "count": len(actions) or 1,
        }
    return None


async def _run_engine(
    agent: Any,
    ctx: AgentContext,
    messages: list[tuple[str, str]],
    *,
    pinned: list[str],
    metric_kind: str,
    with_artifacts: bool,
    thread_id: str = "",
    resume: dict | None = None,
    resume_count: int = 1,
) -> AsyncIterator[dict]:
    """deep 系引擎共用的执行循环：预算续跑、审批中断、指标、收尾帧。

    所有 deepagents 变体（默认 / Pro）事件协议一致，差异只在「怎么装配
    agent」；执行语义必须完全一致，所以收敛到这一个函数，避免两份实现
    各自漂移。

    resume：批准/拒绝决定。给了就发 `Command(resume=...)` 到同一 thread，
    图从断点继续——已执行的工具调用不会重跑（这正是旧实现最大的坑）。
    """
    final_text = ""
    streamed_buf = ""          # 已经作为 delta 下发给前端的正文（可真流式时非空）
    carry: dict[str, Any] = {"messages": [], "todos": None}
    base_messages = list(messages)  # 原始输入：续跑时用它 + carry 重建，不重复拼接
    budgets_used = 0
    payload: Any = (
        Command(resume={"decisions": [resume] * max(1, resume_count)}) if resume
        else {"messages": messages}
    )

    with LlmTimer(metric_kind, get_settings().llm_model) as timer:
        try:
            while True:
                try:
                    pending: dict | None = None
                    async for event in _iter_events(
                        agent, payload, ctx,
                        pinned_skills=pinned, carry=carry, thread_id=thread_id,
                    ):
                        kind = event["kind"]
                        if kind == "__final_text__":
                            final_text = event["text"]
                            streamed_buf = event.get("streamed") or ""
                            continue
                        if kind == "__interrupt__":
                            pending = event["value"]
                            continue
                        yield event

                    if pending:
                        # 图已暂停在「工具执行前」，状态在检查点里；前端批准后
                        # 带同一 session 续跑，不会重做已完成的步骤。
                        yield {"kind": "approval_required", **pending}
                        final_text = (
                            f"任务已暂停：工具 `{pending.get('tool') or '敏感工具'}` "
                            "需要你批准后才能执行。批准后我从当前进度继续，不会重跑已完成的步骤。"
                        )
                        timer.status = "paused"
                        break
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
                    payload = {"messages": messages}
                    if streamed_buf:
                        # 新一轮会从头生成，前端手里那半句话已作废
                        streamed_buf = ""
                        yield {"kind": "answer_reset"}
                    yield {
                        "kind": "note",
                        "content": f"步数预算已用完，自动续接第 {budgets_used + 1}/{MAX_BUDGETS} 轮，从中断处继续…",
                    }
        except ApprovalRequired as approval:
            # react 引擎没有 interrupt：敏感工具在工具内部抛异常，这里暂停。
            # deep 系正常情况下走不到这里（拦截在工具执行之前）。
            yield {
                "kind": "approval_required",
                "tool": approval.tool,
                "args": short(approval.args),
                "args_hash": args_fingerprint(approval.args),
            }
            final_text = (
                f"任务已暂停：工具 `{approval.tool}` 需要你批准后才能执行。"
                "批准后我会自动继续。"
            )
            timer.status = "paused"
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name or "timed out" in str(exc):
                raise RuntimeError(
                    "模型响应超时（任务上下文过长）。建议缩小任务范围，或分多次运行。"
                ) from exc
            raise

    yield final_frame(ctx, final_text, with_artifacts=with_artifacts, streamed=streamed_buf)


# 装配回调：给 ctx、workspace_id、checkpointer，返回 (agent, 开场事件)。
# 开场事件用于"沙箱模式"这类运行前自述，没有就返回空序列。
AgentBuilder = Callable[[AgentContext, str, Any], "tuple[Any, Sequence[dict]]"]


async def _pending_interrupt_count(agent: Any, thread_id: str) -> int:
    """该线程待确认的调用数（0 = 没有中断，需要重跑而不是续跑）。

    HITL 批量模式下 `decisions` 的元素个数必须与待确认调用数一致，所以这里
    返回数量而不是布尔值——模型一轮里同时调两次 code_run 时，只回一个决定
    会被框架判定为不匹配。
    """
    if not thread_id:
        return 0
    try:
        snapshot = await agent.aget_state(thread_config(thread_id, RECURSION_LIMIT))
    except Exception as exc:  # noqa: BLE001 — 检查点不可用按重跑处理，不能让任务失败
        print(f"[agent-deep] 读取检查点失败（按重跑处理）：{type(exc).__name__}: {exc}", flush=True)
        return 0
    for task in getattr(snapshot, "tasks", None) or ():
        interrupts = getattr(task, "interrupts", None)
        if not interrupts:
            continue
        for item in interrupts:
            value = getattr(item, "value", item)
            actions = (value or {}).get("action_requests") if isinstance(value, dict) else None
            if actions:
                return len(actions)
        return 1
    return 0


async def _run_deep(
    task: str,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str | None,
    ctx: AgentContext | None,
    history: list[dict] | None,
    *,
    build: AgentBuilder,
    metric_kind: str,
    pinned_skills: list[str] | None = None,
    thread_id: str = "",
    resume: dict | None = None,
) -> AsyncIterator[dict]:
    """deep 系入口的唯一实现：同步技能 → 装配 → 拼消息 → 执行。

    引擎之间只有 build 不同（后端、额外工具、提示词、开场自述），流程一致，
    所以两份入口共用它——否则"技能同步漏了""开场帧顺序变了"这类漂移迟早出现。

    thread_id + resume：人工确认恢复。批准后带着同一个 thread 发
    `Command(resume=...)`，图从断点继续；检查点已经丢了（进程重启过）时，
    明确告知并退回"按批准重跑"，而不是假装续跑。
    """
    ws = workspace_id or "default"
    with_artifacts = ctx is not None
    ctx = ctx or AgentContext(workspace_id=ws)

    # 热加载：每个任务开始时把 DB 技能同步到工作区 .agent/skills/ 目录，
    # 增删改回滚即时生效；被点名的技能由 pinned_skills 强制装载。
    skill_publisher.sync_skills(ws, owner_id=ctx.owner_id or None)
    pinned = [n for n in (pinned_skills or []) if n]

    # 长期记忆：装配前先按本轮任务召回，提示词里因此带着工作区既有事实。
    # 续跑（resume）时不重新召回——上一轮的提示词已经在检查点里，重拼会导致
    # 同一 thread 里出现两份不同的系统提示。
    if not resume:
        recall_memory(ctx, task)

    # 检查点：有中断才需要，但取用本身很便宜（单例）。放在装配前取，装配
    # 时一并交给 `create_deep_agent`——否则暂停在工具执行前，恢复时找不到图状态。
    saver = await checkpoints.acquire() if _approval_tools() else None

    agent, prelude = build(ctx, ws, saver)
    for event in prelude:
        yield event

    resume_decision: dict | None = None
    resume_count = 1
    if resume:
        pending = await _pending_interrupt_count(agent, thread_id)
        if pending:
            resume_decision, resume_count = resume, pending
        else:
            yield {
                "kind": "note",
                "content": "没有找到可续跑的检查点（服务可能重启过），已按你的批准重新执行。",
            }

    messages: list[tuple[str, str]] = []
    if resume_decision is None:
        messages = await build_messages(task, history, kb_ids, ctx)

    async for event in _run_engine(
        agent, ctx, messages,
        pinned=pinned, metric_kind=metric_kind, with_artifacts=with_artifacts,
        thread_id=thread_id, resume=resume_decision, resume_count=resume_count,
    ):
        yield event


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
    thread_id: str = "",
    resume: dict | None = None,
) -> AsyncIterator[dict]:
    """运行 Deep Agents，产出与 ReAct 引擎完全一致的事件流。

    persona：专家人设（附加系统提示段）；pinned_skills：被点名要强制装载
    的技能 name 清单（对应 SkillsMiddleware 的 pinned_skills 状态键）。
    """
    def build(c: AgentContext, ws: str, saver: Any = None) -> tuple[Any, Sequence[dict]]:
        agent = _build_agent(
            c, tool_ids, kb_ids, ws,
            persona=persona, temperature=temperature,
            interrupt_on=_approval_plan(c),
            checkpointer=saver,
        )
        return agent, ()

    async for event in _run_deep(
        task, tool_ids, kb_ids, workspace_id, ctx, history,
        build=build, metric_kind="agent", pinned_skills=pinned_skills,
        thread_id=thread_id, resume=resume,
    ):
        yield event


__all__ = [
    "DEEP_SYSTEM_PROMPT",
    "MAX_BUDGETS",
    "RECURSION_LIMIT",
    "WorkspaceBackend",
    "run_deep_agent",
]
