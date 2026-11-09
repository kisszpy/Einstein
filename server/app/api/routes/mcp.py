"""MCP 服务器管理：注册、启停、刷新工具清单、直连测试调用。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import app.services.mcp as mcp_service
from app.api.deps import current_uid, current_user, current_workspace
from app.services.mcp import McpServer

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


class ToolApproval(BaseModel):
    tool: str
    approved: bool


def _require_visible(server: McpServer | None, ws: str, uid: str) -> McpServer:
    """可见：平台预置连接（'' ）与自己在当前空间添加的连接。"""
    if server is None or server.workspace_id != ws or server.owner_id not in ("", uid):
        raise HTTPException(404, "MCP 服务器不存在")
    return server


def _require_editable(server: McpServer | None, ws: str, uid: str, is_admin: bool) -> McpServer:
    """可改：自己添加的连接；预置连接只有管理员能改。"""
    _require_visible(server, ws, uid)
    assert server is not None
    if not mcp_service.can_edit(server, uid, is_admin):
        raise HTTPException(403, "预置 MCP 连接只有管理员可以修改")
    return server


@router.get("/servers")
async def list_servers(ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> list[dict]:
    return [mcp_service.server_payload(s) for s in mcp_service.list_servers(ws, uid)]


@router.post("/servers")
async def add_server(
    req: McpServerCreate, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
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
        workspace_id=ws,
        owner_id=uid,
    )
    # 注册即尝试拉取工具清单，结果（含失败原因）随状态返回
    server = (await mcp_service.refresh_server(server.id)) or server
    return mcp_service.server_payload(server)


@router.patch("/servers/{server_id}")
async def patch_server(
    server_id: str, req: McpServerPatch, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    _require_editable(mcp_service.get_server(server_id), ws, uid, user.is_admin)
    server = mcp_service.update_server(server_id, req.model_dump(exclude_none=True))
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    return mcp_service.server_payload(server)


@router.delete("/servers/{server_id}")
async def delete_server(
    server_id: str, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    _require_editable(mcp_service.get_server(server_id), ws, uid, user.is_admin)
    if not mcp_service.remove_server(server_id):
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True}


@router.post("/servers/{server_id}/refresh")
async def refresh(
    server_id: str, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    _require_editable(mcp_service.get_server(server_id), ws, uid, user.is_admin)
    server = await mcp_service.refresh_server(server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    return mcp_service.server_payload(server)


@router.post("/servers/{server_id}/tools/approve")
async def approve_tool(
    server_id: str, req: ToolApproval, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    """工具审批：把工具加入/移出白名单。白名单为空 = 全部放行。"""
    _require_editable(mcp_service.get_server(server_id), ws, uid, user.is_admin)
    server = mcp_service.set_tool_approval(server_id, req.tool.strip(), req.approved)
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    return mcp_service.server_payload(server)


@router.post("/servers/{server_id}/call")
async def call(
    server_id: str, req: McpToolCall, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """直连调用一次工具（管理端调试用）。"""
    _require_visible(mcp_service.get_server(server_id), ws, uid)
    try:
        text = await mcp_service.call_tool(server_id, req.tool, req.arguments)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    return {"result": text}
