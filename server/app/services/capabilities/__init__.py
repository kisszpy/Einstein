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
from app.services.capabilities import code_exec, deck, ppt, sandbox, web

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
        "description": "把结构化内容渲染成带主题版式的 16:9 PPT",
        "tools": ["ppt_create"],
        "default": True,
    },
]

CAPABILITY_TOOL_IDS = {t for cap in CAPABILITIES for t in cap["tools"]}


@dataclass
class AgentContext:
    workspace_id: str
    project: str = ""
    artifacts: list[dict] = field(default_factory=list)

    def note_artifact(self, rel_path: str, kind: str) -> None:
        entry = {"path": rel_path, "kind": kind}
        if entry not in self.artifacts:
            self.artifacts.append(entry)


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
    language: str = Field(default="python", description="python 或 node")
    timeout: int = Field(default=60, description="超时秒数，5-120")


class Slide(BaseModel):
    model_config = {"extra": "allow"}


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
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — 工具边界，兜住所有异常
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
            info = sandbox.create_project(ws, name, template, description)
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
        if get_settings().agent_code_exec:

            def _code_run(code: str = "", path: str = "", language: str = "python", timeout: int = 60) -> str:
                if ctx.project:
                    project = ctx.project
                else:
                    # 无绑定项目时的临时沙盒：纯计算不污染任何用户项目
                    try:
                        sandbox.project_dir(ws, "_scratch")
                    except sandbox.SandboxError:
                        sandbox.create_project(ws, "_scratch", description="智能体临时计算沙盒（自动创建）")
                    project = "_scratch"
                result = code_exec.run_code(ws, project, code, path, language, timeout)
                return sandbox.dumps(result)

            tools.append(StructuredTool.from_function(
                _safe(_code_run), name="code_run",
                description="执行 Python/Node 代码并返回 stdout/stderr。仅用于精确计算、数据处理或验证写好的代码；普通问答和写作不要写代码。无项目时自动落在临时沙盒，不会影响用户项目",
                args_schema=CodeRunArgs, coroutine=None))

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
