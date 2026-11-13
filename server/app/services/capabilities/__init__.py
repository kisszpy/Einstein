"""能力包：把"能干活"的底层服务包装成 LangChain 工具。

一个 AgentContext 对应一次智能体运行：锁定工作区，项目名可被
project_create 在运行中改写（智能体自己建项目后，后续文件/代码工具
自动落在该项目里），artifacts 收集本次运行产出的可下载文件。
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.capabilities import code_exec, deck, ppt, sandbox, slidep, svgdeck, web

# 能力分组（前端展示 / 默认勾选用）
CAPABILITIES = [
    {
        "id": "web",
        "name": "联网",
        "icon": "globe",
        "description": "搜索公开网页并抓取正文，信息不再局限于知识库",
        "tools": ["web_search", "web_fetch"],
        "default": True,
    },
    {
        "id": "files",
        "name": "项目文件",
        "icon": "folder",
        "description": "创建项目、读写文件，产出保存在工作区里可随时下载",
        "tools": ["project_create", "fs_write", "fs_read", "fs_list"],
        "default": True,
    },
    {
        "id": "code",
        "name": "代码执行",
        "icon": "terminal",
        "description": "在项目目录内运行 Python / Node 脚本并回传输出",
        "tools": ["code_run"],
        "default": True,
    },
    {
        "id": "ppt",
        "name": "演示文稿",
        "icon": "presentation",
        "description": "slidep DSL 逐页生成高质量 16:9 PPT（Flexbox 布局+自动校验），svg_compile 走 SVG 原生管线（支持母板页），或 ppt_create 快速出稿",
        "tools": ["slide_upsert", "slide_lint", "ppt_create", "svg_compile"],
        "default": True,
    },
]

CAPABILITY_TOOL_IDS = {t for cap in CAPABILITIES for t in cap["tools"]}


@dataclass
class AgentContext:
    workspace_id: str
    # 当前用户：项目目录、MCP 连接等个人资源都按它做归属过滤
    owner_id: str = ""
    project: str = ""
    artifacts: list[dict] = field(default_factory=list)
    # 本次运行检索到的引用（与 artifacts 同一机制：攒着，收尾帧交给前端）
    citations: list[dict] = field(default_factory=list)
    # 人工确认：本会话已批准过的敏感工具（如 code_run）。仅作为「检查点不可用
    # 时按批准重跑」的兜底；正常路径由框架的 interrupt 拦截（见 hitl）。
    approved_tools: set = field(default_factory=set)
    # 引擎是否用框架的 interrupt 做人工确认。为真时工具本身不再抛异常——
    # 拦截发生在工具执行之前，工具代码保持"被调用就是已批准"的干净语义。
    hitl: bool = False
    # 这次运行是「批准后的续跑」。装配期要靠它区分两件事：本会话的持续授权
    # 可以让新运行不再挂中断，但正在续跑的那一次必须挂——否则检查点里那条
    # 待确认会永远悬着，图再也走不下去。
    resuming: bool = False
    # 本轮召回到的长期记忆（人设之外的工作区事实）。由引擎在装配前填好，
    # 提示词构建器负责拼进系统提示——智能体因此也带背景，而不是每次从零开始。
    memory: list[str] = field(default_factory=list)
    # 本轮使用的模型（models 表 id）。空 = 平台默认 → .env。
    # 放这里而不是逐个函数加参数：装配点在 _build_agent/_get_model 深处，
    # 一路透传要改四层签名，而"这轮用什么模型"本来就是上下文的一部分。
    model_id: str = ""

    def note_artifact(self, rel_path: str, kind: str) -> None:
        entry = {"path": rel_path, "kind": kind}
        if entry not in self.artifacts:
            self.artifacts.append(entry)


class ApprovalRequired(Exception):
    """敏感工具未经批准就被调用：携带工具名与参数，由引擎转成暂停事件。"""

    def __init__(self, tool: str, args: dict) -> None:
        self.tool = tool
        self.args = args
        super().__init__(f"工具 {tool} 需要人工批准")


def _approval_tools() -> set[str]:
    """需要人工批准的工具名集合（APP_AGENT_APPROVAL_TOOLS，逗号分隔；空=关闭）。"""
    raw = (get_settings().agent_approval_tools or "").strip()
    return {t.strip() for t in raw.split(",") if t.strip()}


def _artifact_kind(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("html", "htm"):
        return "html"
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"):
        return "image"
    if ext == "pdf":
        return "pdf"
    if ext == "pptx":
        return "pptx"
    if ext in ("md", "markdown"):
        return "markdown"
    return "file"


# ---------- 参数模型 ----------

class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索查询词")
    max_results: int = Field(default=6, description="返回条数，1-12")


class WebFetchArgs(BaseModel):
    url: str = Field(description="要抓取的网页地址（http/https）")
    max_chars: int = Field(default=2500, description="正文字符上限，够用即可，建议 2000-3000")


class ProjectCreateArgs(BaseModel):
    name: str = Field(description="项目名（字母/数字/中文/连字符）")
    template: str = Field(default="blank", description="脚手架：blank / python / fastapi / web")
    description: str = Field(default="", description="一句话描述，写进 README")


class FsWriteArgs(BaseModel):
    path: str = Field(description="项目内相对路径，如 src/main.py")
    content: str = Field(description="完整文件内容")


class FsReadArgs(BaseModel):
    path: str = Field(description="项目内相对路径")
    max_chars: int = Field(default=12000, description="读取字符上限")


class FsListArgs(BaseModel):
    path: str = Field(default="", description="子目录，留空看整个项目")


class CodeRunArgs(BaseModel):
    code: str = Field(default="", description="要执行的代码（与 path 二选一）")
    path: str = Field(default="", description="执行项目内已有脚本")
    language: str = Field(
        default="python",
        description="python | node | java | maven。java 跑单文件源码（java Foo.java，只含 main 类）；"
        "maven 在含 pom.xml 的项目里跑 mvn 命令（code 里写参数，如 package）",
    )
    timeout: int = Field(default=60, description="超时秒数，5-120")


class Slide(BaseModel):
    model_config = {"extra": "allow"}


class SlideUpsertArgs(BaseModel):
    pptx_path: str = Field(description="项目内 PPTX 相对路径，如 deck/产品发布.pptx（不存在会自动创建）")
    dsl_file: str = Field(default="", description="项目内 DSL 文件相对路径（首选，如 slides/01.slide）")
    dsl: str = Field(default="", description="内联 DSL 字符串（与 dsl_file 二选一）")
    page_index: int = Field(default=-1, description="-1=追加新页；>=0 替换该页（0-based）")


class SlideLintArgs(BaseModel):
    pptx_path: str = Field(description="项目内 PPTX 相对路径（作为校验上下文）")
    dsl_file: str = Field(default="", description="项目内 DSL 文件相对路径")
    dsl: str = Field(default="", description="内联 DSL 字符串（与 dsl_file 二选一）")
    page_index: int = Field(default=0, description="0-based 页码上下文")


class SvgCompileArgs(BaseModel):
    pptx_path: str = Field(description="项目内 PPTX 相对路径，如 deck/产品发布.pptx（编译输出，不存在自动创建）")


class PptCreateArgs(BaseModel):
    title: str = Field(description="演示文稿标题")
    subtitle: str = Field(default="", description="副标题")
    author: str = Field(default="", description="署名，显示在封面")
    theme: str = Field(default="midnight", description="配色：midnight / graphite / ocean / sunset / forest")
    filename: str = Field(default="", description="文件名（不含扩展名）")
    slides: list[dict] = Field(
        description=(
            "幻灯片数组。每页一个对象，可用 layout："
            "title（封面，title/subtitle）、section（章节页，number/title/subtitle）、"
            "bullets（要点页，kicker/title/bullets 列表，'-' 开头为二级要点）、"
            "two_col（两栏，columns:[{title,bullets}]）、quote（引用，quote/subtitle）、"
            "table（表格，headers/rows）、end（结尾页）。"
            "不写 layout 时自动推断。一份完整的 deck 通常 6-10 页。"
        )
    )


# ---------- 工具工厂 ----------

def _err(exc: Exception) -> str:
    return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def _safe(fn):
    """工具异常一律转成结果返回。

    单个网页 403、某个项目不存在都不该中断整轮任务——模型读到
    {"ok": false, "error": ...} 后可以换来源或换做法继续干。

    **`ApprovalRequired` 必须放行**：它是"暂停等用户确认"的控制流，不是工具
    失败。被这里兜住的话，引擎永远收不到暂停信号，用户也永远看不到批准框，
    任务只会收到一个 `{"ok": false, ...}` 然后自己找别的路——人工确认形同虚设。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApprovalRequired:
            raise
        except Exception as exc:  # noqa: BLE001 — 工具边界，兜住其余所有异常
            return _err(exc)

    return wrapper


def make_tools(ctx: AgentContext, tool_ids: list[str]) -> list[StructuredTool]:
    """按勾选的能力构建工具；文件类工具共享同一个 ctx。"""
    wanted = set(tool_ids) & CAPABILITY_TOOL_IDS
    tools: list[StructuredTool] = []
    ws = ctx.workspace_id

    def need_project() -> str:
        if not ctx.project:
            raise sandbox.SandboxError("还没有活动项目：先调用 project_create，或由用户在界面上指定项目")
        return ctx.project

    if "web_search" in wanted:
        def _web_search(query: str, max_results: int = 6) -> str:
            return web.web_search(query, max_results)

        tools.append(StructuredTool.from_function(
            _safe(_web_search), name="web_search",
            description="搜索公开网页，返回标题/链接/摘要。查时效性信息、行业数据、外部事实时使用",
            args_schema=WebSearchArgs, coroutine=None))

    if "web_fetch" in wanted:
        def _web_fetch(url: str, max_chars: int = 6000) -> str:
            return web.web_fetch(url, max_chars)

        tools.append(StructuredTool.from_function(
            _safe(_web_fetch), name="web_fetch",
            description="抓取网页正文（自动去导航/脚本），配合 web_search 核实细节",
            args_schema=WebFetchArgs, coroutine=None))

    if "project_create" in wanted:
        def _project_create(name: str, template: str = "blank", description: str = "") -> str:
            # 延迟导入：projects 依赖沙箱，模块顶层互相 import 会成环
            from app.services import projects

            # 项目登记在当前用户名下：同空间的其他成员看不到、也进不来
            info = projects.create_project(
                ws, name, template, description, owner_id=ctx.owner_id
            )
            ctx.project = info["project"]
            return sandbox.dumps(info)

        tools.append(StructuredTool.from_function(
            _safe(_project_create), name="project_create",
            description=f"在当前工作区创建项目（脚手架：{'/'.join(sandbox.TEMPLATES)}）。仅当用户要文件交付（代码工程/文档/PPT）时才创建；纯问答不要建项目",
            args_schema=ProjectCreateArgs, coroutine=None))

    if "fs_write" in wanted:
        def _fs_write(path: str, content: str) -> str:
            project = need_project()
            info = sandbox.write_file(ws, project, path, content)
            # 写出的文件就是产物：推给前端，右侧预览面板可以就地打开
            ctx.note_artifact(info.get("path") or path, _artifact_kind(info.get("path") or path))
            return sandbox.dumps(info)

        tools.append(StructuredTool.from_function(
            _safe(_fs_write), name="fs_write",
            description="把完整内容写入项目内文件（自动创建目录）。用于需要落盘的交付物（代码/文档/PPT 数据）",
            args_schema=FsWriteArgs, coroutine=None))

    if "fs_read" in wanted:
        def _fs_read(path: str, max_chars: int = 12000) -> str:
            project = need_project()
            return sandbox.dumps(sandbox.read_file(ws, project, path, max_chars))

        tools.append(StructuredTool.from_function(
            _safe(_fs_read), name="fs_read",
            description="读取项目内文件内容",
            args_schema=FsReadArgs, coroutine=None))

    if "fs_list" in wanted:
        def _fs_list(path: str = "") -> str:
            project = need_project()
            return sandbox.dumps(sandbox.list_files(ws, project, path))

        tools.append(StructuredTool.from_function(
            _safe(_fs_list), name="fs_list",
            description="列出项目文件树",
            args_schema=FsListArgs, coroutine=None))

    if "code_run" in wanted:
        # 装配期就判「有没有真沙箱」：没有容器又没显式允许宿主执行时，这个工具
        # 不能挂——否则模型会先试一次、拿到一句「已拒绝执行」，白跑一轮还打断计划。
        # 用户会在开跑时收到一条 note（见 agent_deep._run_deep），不是无声消失。
        if get_settings().agent_code_exec and code_exec.available()[0]:

            def _code_run(code: str = "", path: str = "", language: str = "python", timeout: int = 60) -> str:
                # 人工确认门：deep 系引擎由框架 interrupt 在「执行前」拦截，工具
                # 被调用即代表已批准；react 引擎没有 interrupt，只能在工具里抛。
                #
                # needs_approval() 决定「这一步到底要不要问」——隔离沙箱里不问，
                # 宿主进程里必问。判断依据是环境而不是工具名，所以它在这里和
                # agent_deep._approval_plan 里得到同一个答案。
                if (
                    not ctx.hitl
                    and "code_run" in _approval_tools()
                    and "code_run" not in ctx.approved_tools
                    and code_exec.needs_approval()
                ):
                    raise ApprovalRequired("code_run", {"code": code[:400], "language": language})
                if ctx.project:
                    project = ctx.project
                else:
                    # 无绑定项目时的临时沙盒：纯计算不污染任何用户项目。
                    # 按用户各留一个，避免两个人的临时文件互相看得见
                    from app.services import projects

                    scratch = f"_scratch-{ctx.owner_id[:8]}" if ctx.owner_id else "_scratch"
                    project = projects.ensure_internal(
                        ws, scratch, ctx.owner_id, description="智能体临时计算沙盒（自动创建）"
                    )
                result = code_exec.run_code(ws, project, code, path, language, timeout)
                # 代码跑出来的文件已经同步回项目目录，但它们不是 fs_write 写的，
                # 没人把它们当产物——前端因此不出现卡片、done 帧里也没有。
                # 用户看到的是「跑完了但什么都没交付」。这里逐条补上。
                for rel in result.get("files") or []:
                    ctx.note_artifact(rel, _artifact_kind(rel))
                return sandbox.dumps(result)

            tools.append(StructuredTool.from_function(
                _safe(_code_run), name="code_run",
                description=(
                    "执行代码并返回 stdout/stderr，支持 python / node / java / maven。"
                    "仅用于精确计算、数据处理或验证写好的代码；普通问答和写作不要写代码。"
                    "无项目时自动落在临时沙盒，不会影响用户项目。"
                    "要产出交付文件（报告/图表/PPT）时，用相对路径写在当前工作目录；"
                    "写完会自动同步回项目并列为产物。"
                    + code_exec.capability_note()
                ),
                args_schema=CodeRunArgs, coroutine=None))

    if "slide_upsert" in wanted:
        def _slide_common(req: "SlideUpsertArgs | SlideLintArgs") -> tuple[Path, Path, str]:
            project = need_project()
            root = sandbox.project_dir(ws, project)
            out = sandbox.resolve_in(root, req.pptx_path)
            if req.dsl_file:
                dsl_path = sandbox.resolve_in(root, req.dsl_file)
                dsl = dsl_path.read_text(encoding="utf-8")
            elif req.dsl:
                dsl = req.dsl
            else:
                raise sandbox.SandboxError("dsl_file 与 dsl 至少提供一个")
            return root, out, dsl

        def _slide_upsert(pptx_path: str, dsl_file: str = "", dsl: str = "", page_index: int = -1) -> str:
            root, out, text = _slide_common(SlideUpsertArgs(
                pptx_path=pptx_path, dsl_file=dsl_file, dsl=dsl, page_index=page_index))
            out.parent.mkdir(parents=True, exist_ok=True)
            result = slidep.upsert(out, text, page_index)
            if result.get("ok"):
                ctx.note_artifact(pptx_path, "pptx")
                # 登记逐页 DSL 来源：浏览页编辑器据此提供无损 DSL 编辑（改→lint→重编译）
                deck.remember_dsl_page(root, pptx_path, page_index, dsl_file, text)
            return sandbox.dumps(result)

        def _slide_lint(pptx_path: str, dsl_file: str = "", dsl: str = "", page_index: int = 0) -> str:
            _root, out, text = _slide_common(SlideLintArgs(
                pptx_path=pptx_path, dsl_file=dsl_file, dsl=dsl, page_index=page_index))
            return sandbox.dumps(slidep.lint(out, text, page_index))

        tools.append(StructuredTool.from_function(
            _safe(_slide_upsert), name="slide_upsert",
            description=(
                "把一页 slidep DSL 编译成 PPT 页并插入/替换到项目内 PPTX（不存在自动创建）。"
                "DSL 是 JSX 风格：<Slide width={1280} height={720}> 里用 Box/Text 等组件、"
                "内联 style 写版式，支持 Flexbox 自动布局。生成高质量 PPT 的标准路径："
                "先 fs_write 每页 DSL 到 slides/NN.slide，再逐页 upsert + lint 校验修复"
            ),
            args_schema=SlideUpsertArgs, coroutine=None))

        tools.append(StructuredTool.from_function(
            _safe(_slide_lint), name="slide_lint",
            description=(
                "对一页 slidep DSL 做本地布局校验（文本溢出/组件重叠/零尺寸等）。"
                "upsert 后必须 lint：diagnostics 有 error 时修 DSL 再 upsert 同一页，直到无 error"
            ),
            args_schema=SlideLintArgs, coroutine=None))

    if "svg_compile" in wanted:
        def _svg_compile(pptx_path: str) -> str:
            project = need_project()
            root = sandbox.project_dir(ws, project)
            rel = pptx_path.strip().replace("\\", "/")
            if not rel.lower().endswith(".pptx"):
                raise sandbox.SandboxError("输出路径必须以 .pptx 结尾")
            result = svgdeck.compile_deck(root, rel)
            ctx.note_artifact(rel, "pptx")
            return sandbox.dumps(result)

        tools.append(StructuredTool.from_function(
            _safe(_svg_compile), name="svg_compile",
            description=(
                "把项目 slides/ 目录里的逐页 SVG（01.svg、02.svg…，画布固定 1280×720，纯 SVG 1.1）"
                "一次性编译成原生形状 PPTX——文字是文本框、图形是矢量形状、渐变/圆角全部保留，"
                "在 PowerPoint/WPS 里完全可继续编辑（不是图片贴图）。"
                "可选母板：slides/master.svg 会作为底层合成进每一页，统一背景/页眉/装饰，"
                "让整份 PPT 有设计规范（母板里画好背景与装饰元素，页面 SVG 只画内容层）。"
                "生成高质量 PPT 的推荐路径之一：fs_write 写好全部页面 SVG（及可选 master.svg）"
                "→ svg_compile 一次编译。改页时改对应 NN.svg 后重新 compile 即可"
            ),
            args_schema=SvgCompileArgs, coroutine=None))

    if "ppt_create" in wanted:
        def _ppt_create(
            title: str, subtitle: str = "", author: str = "", theme: str = "midnight",
            filename: str = "", slides: list[dict] | None = None,
        ) -> str:
            project = need_project()
            payload = {
                "title": title, "subtitle": subtitle, "author": author,
                "theme": theme, "filename": filename, "slides": slides or [],
            }
            rel = f"deck/{ppt._safe_filename(payload['filename'] or title)}.pptx"
            root = sandbox.project_dir(ws, project)
            out: Path = sandbox.resolve_in(root, rel)
            info = ppt.write_pptx(payload, out)
            # 记住结构化来源：之后在内置浏览器里编辑这份 PPT 可无损还原
            deck.remember(root, rel, payload)
            info["ok"] = True
            info["path"] = rel
            del info["file"]
            ctx.note_artifact(rel, "pptx")
            return sandbox.dumps(info)

        tools.append(StructuredTool.from_function(
            _safe(_ppt_create), name="ppt_create",
            description=(
                "生成 16:9 演示文稿。slides 每页一个 dict："
                "封面页 {title, subtitle, variant?}；"
                "要点页 {layout:'bullets', title, bullets:[…]}（3-6 条、每条一句话）；"
                "大数字页 {layout:'stat', title, stats:[{value:'38%', label, note}]}（关键指标优先用它）；"
                "条形图页 {layout:'chart', title, items:[{label, value:'-6.4%'}], note?}（可比较的量化数据用它）；"
                "时间线 {layout:'timeline', title, steps:[{label, text}]}（3-5 步）；"
                "卡片矩阵 {layout:'grid', title, cards:[{title, text}]}（2-6 张）；"
                "对比页 {layout:'vs', title, left:{title, bullets}, right:{title, bullets}}；"
                "两栏 {layout:'two_col', title, columns:[…]}、引用 {layout:'quote', quote, subtitle}、"
                "表格 {layout:'table', title, headers, rows}、章节 {layout:'section', title}、结尾 {layout:'end'}。"
                "版式按内容选、整份有变化，不要整份都是要点页"
            ),
            args_schema=PptCreateArgs, coroutine=None))

    return tools
