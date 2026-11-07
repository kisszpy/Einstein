"""MCP 服务器注册表与客户端。

配置持久化在 data/mcp_servers.json（与知识库 JSON 适配器同一替换点：
生产换 PostgreSQL 时接口不变）。连接按需建立——刷新工具清单、执行调用
时开一个会话，用完即断；开发期最省心，生产可替换为常驻连接池。

工具 ID 约定：`mcp:{server_id}:{tool_name}`，Tool Registry 按 ID 前缀
路由，上层（智能体/对话）对内置工具与 MCP 工具无感知差异。
"""

import asyncio
import json
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob
from typing import Literal

import mcp.client.stdio as _stdio
import mcp.client.streamable_http as _http
from mcp import ClientSession, StdioServerParameters
from pydantic import BaseModel, Field

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_STORE_PATH = _DATA_DIR / "mcp_servers.json"
_STATE = blob("mcp", _STORE_PATH)

TOOL_CALL_TIMEOUT = 120  # 单次工具调用上限（秒）
LIST_TIMEOUT = 20        # 刷新工具清单上限（秒）


class McpToolDef(BaseModel):
    name: str
    description: str = ""
    schema_: dict = Field(default_factory=dict, alias="schema")
    model_config = {"populate_by_name": True}


class McpServer(BaseModel):
    id: str
    name: str
    transport: Literal["stdio", "http"] = "stdio"
    # stdio
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http (streamable)
    url: str = ""

    enabled: bool = True
    # 运行时缓存（刷新后写入）
    status: Literal["unknown", "ok", "error"] = "unknown"
    tools: list[McpToolDef] = Field(default_factory=list)
    last_error: str = ""
    refreshed_at: str = ""
    workspace_id: str = "default"

    def connection_summary(self) -> str:
        if self.transport == "stdio":
            return " ".join([self.command, *self.args]).strip()
        return self.url


# ---------- 持久化 ----------

_servers: dict[str, McpServer] | None = None
_lock = threading.Lock()


def _load() -> dict[str, McpServer]:
    try:
        payload = _STATE.read() or {}
        return {sid: McpServer(**v) for sid, v in payload.items()}
    except Exception:  # noqa: BLE001 — 损坏则重建
        return {}


def _save(servers: dict[str, McpServer]) -> None:
    payload = {sid: s.model_dump(by_alias=True) for sid, s in servers.items()}
    _STATE.write(payload)


def _registry() -> dict[str, McpServer]:
    global _servers
    with _lock:
        if _servers is None:
            _servers = _load()
        return _servers


# ---------- CRUD ----------

def list_servers(workspace_id: str | None = None) -> list[McpServer]:
    rows = list(_registry().values())
    if workspace_id:
        rows = [s for s in rows if s.workspace_id == workspace_id]
    return rows


def get_server(server_id: str) -> McpServer | None:
    return _registry().get(server_id)


def add_server(
    name: str,
    transport: Literal["stdio", "http"],
    command: str = "",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    url: str = "",
    workspace_id: str = "default",
) -> McpServer:
    server = McpServer(
        id=uuid.uuid4().hex[:12],
        name=name,
        transport=transport,
        command=command,
        args=args or [],
        env=env or {},
        url=url,
        workspace_id=workspace_id,
    )
    registry = _registry()
    with _lock:
        registry[server.id] = server
        _save(registry)
    return server


def update_server(server_id: str, patch: dict) -> McpServer | None:
    registry = _registry()
    server = registry.get(server_id)
    if server is None:
        return None
    allowed = {"name", "enabled", "command", "args", "env", "url", "transport"}
    updates = {k: v for k, v in patch.items() if k in allowed}
    updated = server.model_copy(update=updates)
    # 配置变更后工具缓存失效
    if {"command", "args", "env", "url", "transport"} & updates.keys():
        updated = updated.model_copy(
            update={"tools": [], "status": "unknown", "last_error": "", "refreshed_at": ""}
        )
    with _lock:
        registry[server_id] = updated
        _save(registry)
    return updated


def remove_server(server_id: str) -> bool:
    registry = _registry()
    if server_id not in registry:
        return False
    with _lock:
        del registry[server_id]
        _save(registry)
    return True


# ---------- 连接与调用 ----------

@asynccontextmanager
async def _connect(server: McpServer):
    """按传输方式建立会话。用 asyncio.wait_for 兜底防止握手挂死。"""
    if server.transport == "stdio":
        # 合并环境变量：stdio 子进程通常需要继承 PATH 才能解析命令
        env = {**_stdio.get_default_environment(), **server.env}
        params = StdioServerParameters(command=server.command, args=server.args, env=env)
        async with _stdio.stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), LIST_TIMEOUT)
                yield session
    else:
        async with _http.streamable_http_client(server.url) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), LIST_TIMEOUT)
                yield session


def _fmt_exc(exc: BaseException) -> str:
    """展开 ExceptionGroup，把子异常也带出来（TaskGroup 常包一层）。"""
    parts = []
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if isinstance(e, BaseExceptionGroup):
            stack.extend(e.exceptions)
        else:
            parts.append(f"{type(e).__name__}: {e}")
    return " | ".join(parts) or f"{type(exc).__name__}: {exc}"


async def refresh_server(server_id: str) -> McpServer | None:
    """连接服务器拉取工具清单并缓存。"""
    registry = _registry()
    server = registry.get(server_id)
    if server is None:
        return None
    try:
        async with _connect(server) as session:
            result = await asyncio.wait_for(session.list_tools(), LIST_TIMEOUT)
            tools = [
                McpToolDef(
                    name=t.name,
                    description=t.description or "",
                    schema=(t.input_schema or {}) if isinstance(t.input_schema, dict) else {},
                )
                for t in result.tools
            ]
        updated = server.model_copy(
            update={
                "tools": tools,
                "status": "ok",
                "last_error": "",
                "refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    except Exception as exc:  # noqa: BLE001 — 连接失败记入状态，不抛
        updated = server.model_copy(
            update={
                "tools": [],
                "status": "error",
                "last_error": _fmt_exc(exc),
                "refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    with _lock:
        registry[server_id] = updated
        _save(registry)
    return updated


async def call_tool(server_id: str, tool_name: str, arguments: dict) -> str:
    """执行一次 MCP 工具调用，返回文本结果。"""
    server = _registry().get(server_id)
    if server is None:
        raise ValueError(f"MCP 服务器不存在: {server_id}")
    if not server.enabled:
        raise ValueError(f"MCP 服务器已停用: {server.name}")
    async with _connect(server) as session:
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments=arguments), TOOL_CALL_TIMEOUT
        )
    parts: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    text = "\n".join(parts) if parts else json.dumps(
        {"isError": bool(result.is_error)}, ensure_ascii=False
    )
    if result.is_error:
        text = f"[MCP 工具报错] {text}"
    return text


def server_payload(server: McpServer) -> dict:
    d = server.model_dump(by_alias=True)
    d["connection"] = server.connection_summary()
    return d
