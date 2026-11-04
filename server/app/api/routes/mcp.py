"""MCP 服务器管理：注册、启停、刷新工具清单、直连测试调用。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import app.services.mcp as mcp_service

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpServerCreate(BaseModel):
    name: str
    transport: str = "stdio"  # stdio | http
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""


class McpServerPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class McpToolCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)


@router.get("/servers")
async def list_servers() -> list[dict]:
    return [mcp_service.server_payload(s) for s in mcp_service.list_servers()]


@router.post("/servers")
async def add_server(req: McpServerCreate) -> dict:
    if req.transport not in ("stdio", "http"):
        raise HTTPException(400, "transport 只支持 stdio 或 http")
    if req.transport == "stdio" and not req.command:
        raise HTTPException(400, "stdio 传输必须提供 command")
    if req.transport == "http" and not req.url:
        raise HTTPException(400, "http 传输必须提供 url")
    server = mcp_service.add_server(
        name=req.name,
        transport=req.transport,  # type: ignore[arg-type]
        command=req.command,
        args=req.args,
        env=req.env,
        url=req.url,
    )
    # 注册即尝试拉取工具清单，结果（含失败原因）随状态返回
    server = (await mcp_service.refresh_server(server.id)) or server
    return mcp_service.server_payload(server)


@router.patch("/servers/{server_id}")
async def patch_server(server_id: str, req: McpServerPatch) -> dict:
    server = mcp_service.update_server(server_id, req.model_dump(exclude_none=True))
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    return mcp_service.server_payload(server)


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str) -> dict:
    if not mcp_service.remove_server(server_id):
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True}


@router.post("/servers/{server_id}/refresh")
async def refresh(server_id: str) -> dict:
    server = await mcp_service.refresh_server(server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    return mcp_service.server_payload(server)


@router.post("/servers/{server_id}/call")
async def call(server_id: str, req: McpToolCall) -> dict:
    """直连调用一次工具（管理端调试用）。"""
    try:
        text = await mcp_service.call_tool(server_id, req.tool, req.arguments)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    return {"result": text}
