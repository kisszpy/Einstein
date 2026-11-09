"""工具注册表。

每个工具 = 名称/描述/OpenAI function schema/可调用体。
来源分三层：内置工具、知识库派生（kb_search，随挂载自动出现）、
MCP 外部工具（ID 形如 mcp:{server_id}:{tool_name}，经 MCP 服务
同步后以同样结构注册进来，上层无感）。
"""

import ast
import operator
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

import app.services.mcp as mcp_service
from app.services.capabilities import CAPABILITIES, CAPABILITY_TOOL_IDS, AgentContext, make_tools
from app.services.rag import search
from app.services.store import get_store

MCP_TOOL_PREFIX = "mcp:"

# ---------- 工具实现 ----------

_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
}


def _safe_calc(expr: str) -> str:
    """AST 白名单四则运算，不碰 eval。"""
    def ev(node: ast.AST) -> float:
        match node:
            case ast.Constant(value=int() | float() as v):
                return float(v)
            case ast.BinOp(op=op):
                return _OPS[type(op)](ev(node.left), ev(node.right))  # type: ignore[attr-defined]
            case ast.UnaryOp(op=op):
                return _OPS[type(op)](ev(node.operand))  # type: ignore[attr-defined]
            case _:
                raise ValueError(f"不支持的表达式元素: {type(node).__name__}")

    tree = ast.parse(expr, mode="eval")
    return f"{expr} = {ev(tree.body)}"


class CalcArgs(BaseModel):
    expression: str = Field(description="四则运算表达式，如 (3+4)*12")


def calculator(expression: str) -> str:
    return _safe_calc(expression)


def current_time() -> str:
    now = datetime.now(timezone.utc)
    return f"当前 UTC 时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"


class TextStatsArgs(BaseModel):
    text: str = Field(description="要统计的文本")


def text_stats(text: str) -> str:
    chars = len(text)
    words = len(text.split())
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return f"总字符 {chars}，词数 {words}，中文字符 {cn}"


def _make_kb_search(kb_ids: list[str]):
    class KBSearchArgs(BaseModel):
        query: str = Field(description="检索查询")

    async def kb_search(query: str) -> str:
        hits = await search(get_store(), kb_ids, query, top_k=3)
        if not hits:
            return "知识库中没有找到相关内容。"
        return "\n\n".join(f"[{h.document_name}] {h.text}" for h in hits)

    return StructuredTool.from_function(
        coroutine=kb_search,
        name="kb_search",
        description="在挂载的知识库中检索相关资料。挂载了知识库时这是第一信息源，"
                    "回答前必须先检索，知识库查不到才考虑 web_search",
        args_schema=KBSearchArgs,
    )


def kb_directive(kb_ids: list[str]) -> str:
    """挂载知识库时附加到系统提示词的硬规则。

    之前 kb_search 只是"可用的工具"，模型经常直接用参数知识回答，
    知识库挂了也白挂；用户预期是"选了知识库就先从知识库检索"。
    这段指令 + run 入口的预取检索（preflight_kb）共同保证这一点。
    """
    return (
        f"\n【知识库（已挂载 {len(kb_ids)} 个，第一优先级信息源）】\n"
        "1. 对话开头的【知识库预取】是系统替你做好的首轮检索，回答先基于它。\n"
        "2. 预取内容不够答、或是多轮追问出现新问题点时，先 kb_search 补检索"
        "（可以换措辞多试一两次），kb_search 查不到才允许 web_search。\n"
        "3. 知识库有相关内容时，答案必须以知识库为准并注明文档名；"
        "绝不拿自己的参数知识顶替知识库里已有的说法。\n"
        "4. 知识库内容只能通过 kb_search 获取，不要在工作区文件系统里"
        "grep/glob/read_file 找知识库的文档——它们不在那里。\n"
        "5. 知识库未命中相关内容时，明说「知识库中未找到」，再走 web_search。"
    )


async def preflight_kb(kb_ids: list[str], task: str, top_k: int = 4) -> str | None:
    """首轮强制检索：把命中结果直接注入上下文。

    只靠提示词，模型仍会漏调 kb_search；预取把"先检索"变成确定性行为。
    追问轮（有 history）不预取——「再改一下」这类任务拿去检索只会带回噪音，
    那时由提示词规则驱动模型按需调 kb_search。
    """
    if not kb_ids or not task.strip():
        return None
    try:
        hits = await search(get_store(), kb_ids, task, top_k=top_k)
    except Exception:  # noqa: BLE001 — 检索服务挂了不阻塞对话，模型还能自己补调
        return None
    if not hits:
        return "[知识库预取] 知识库中没有检索到与任务相关的内容。请先告诉用户知识库未命中，再考虑其他信息源。"
    body = "\n\n".join(f"[{h.document_name}] {h.text}" for h in hits)
    return (
        f"[知识库预取] 系统已按任务在知识库中检索到 {len(hits)} 条相关内容，"
        f"回答请以这些为准并注明文档名：\n\n{body}"
    )


# ---------- MCP 工具适配 ----------

_JSON_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _schema_to_args_model(tool_name: str, schema: dict) -> type[BaseModel]:
    """把 MCP 工具的 JSON Schema 转成 Pydantic 模型（LangChain 需要）。"""
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    fields: dict[str, Any] = {}
    for key, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        py_type = _JSON_TYPE_MAP.get(spec.get("type"), str)
        desc = spec.get("description", "")
        if key in required:
            fields[key] = (py_type, Field(description=desc))
        else:
            fields[key] = (py_type | None, Field(default=None, description=desc))
    safe_name = f"Mcp_{tool_name}_Args".replace("-", "_").replace(".", "_")
    return create_model(safe_name, **fields)


def _make_mcp_tool(server_id: str, server_name: str, tool: "mcp_service.McpToolDef") -> StructuredTool:
    async def _call(**kwargs) -> str:
        return await mcp_service.call_tool(server_id, tool.name, kwargs)

    return StructuredTool.from_function(
        coroutine=_call,
        name=tool.name,
        description=f"[MCP:{server_name}] {tool.description or tool.name}",
        args_schema=_schema_to_args_model(tool.name, tool.schema_),
    )


# ---------- 注册表 ----------

def _tool_def(name: str, description: str, schema: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


def list_tools(workspace_id: str | None = None, owner_id: str | None = None) -> dict:
    """返回可展示的工具清单：基础工具 + 能力分组 + 已同步的 MCP 工具。"""
    tools = [
        {
            "id": "calculator",
            "name": "计算器",
            "description": "安全的四则运算（AST 白名单，非 eval）",
            "group": "basic",
        },
        {
            "id": "current_time",
            "name": "当前时间",
            "description": "查询当前 UTC 时间",
            "group": "basic",
        },
        {
            "id": "text_stats",
            "name": "文本统计",
            "description": "统计文本的字符数、词数、中文字符数",
            "group": "basic",
        },
    ]
    for cap in CAPABILITIES:
        tools.append(
            {
                "id": cap["id"],
                "name": cap["name"],
                "description": cap["description"],
                "group": "capability",
                "tools": cap["tools"],
                "default": cap["default"],
            }
        )
    for server in mcp_service.list_servers(workspace_id, owner_id):
        if not server.enabled:
            continue
        for t in server.tools:
            tools.append(
                {
                    "id": f"{MCP_TOOL_PREFIX}{server.id}:{t.name}",
                    "name": t.name,
                    "description": f"[MCP · {server.name}] {t.description or t.name}",
                    "group": "mcp",
                }
            )
    return {"tools": tools, "capabilities": CAPABILITIES}


def build_lc_tools(
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str | None = None,
    ctx: AgentContext | None = None,
    owner_id: str | None = None,
) -> list[StructuredTool]:
    """按选中的工具 ID 构建 LangChain 工具实例。

    挂载了知识库即自动注册 kb_search，无需显式勾选——检索的可用性
    由资源挂载决定，不是工具开关。workspace_id 用于把 MCP 工具限制在
    当前工作区自己的服务器上；ctx 存在时按勾选构建工作能力工具
    （联网/项目文件/代码/PPT），产物都收进 ctx.artifacts。
    """
    lc_tools: list[StructuredTool] = []
    # 归属：显式参数优先，其次取执行上下文里带的当前用户
    owner = owner_id if owner_id is not None else (ctx.owner_id if ctx else None)
    mcp_ids = [tid for tid in tool_ids if tid.startswith(MCP_TOOL_PREFIX)]
    builtin_ids = [tid for tid in tool_ids if not tid.startswith(MCP_TOOL_PREFIX)]

    if "calculator" in builtin_ids:
        lc_tools.append(StructuredTool.from_function(calculator, name="calculator",
                        description="计算四则运算表达式", args_schema=CalcArgs))
    if "current_time" in builtin_ids:
        lc_tools.append(StructuredTool.from_function(current_time, name="current_time",
                        description="查询当前 UTC 时间"))
    if "text_stats" in builtin_ids:
        lc_tools.append(StructuredTool.from_function(text_stats, name="text_stats",
                        description="统计文本字符/词数", args_schema=TextStatsArgs))
    if kb_ids:
        lc_tools.append(_make_kb_search(kb_ids))

    if ctx is not None:
        lc_tools.extend(make_tools(ctx, builtin_ids))

    if mcp_ids:
        wanted: dict[str, list[str]] = {}
        for tid in mcp_ids:
            _, server_id, tool_name = tid.split(":", 2)
            wanted.setdefault(server_id, []).append(tool_name)
        for server_id, tool_names in wanted.items():
            if owner is not None and workspace_id:
                # 只能调用自己可见的连接（预置 '' + 自己的），别人的一律当不存在
                server = mcp_service.visible_server(server_id, workspace_id, owner)
            else:
                server = mcp_service.get_server(server_id)
            if server is None or not server.enabled:
                continue
            if workspace_id and server.workspace_id != workspace_id:
                continue
            for tool in server.tools:
                if tool.name in tool_names:
                    lc_tools.append(_make_mcp_tool(server_id, server.name, tool))
    return lc_tools
