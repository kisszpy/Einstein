"""平台管理：工作区、API Key、审计日志。

控制台使用，走 X-Workspace 上下文；工作区列表本身是全局的（用于切换器）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import allowed_workspaces, current_admin, current_user, current_workspace
from app.services import apikeys, audit, users, workspaces
from app.services.eval import list_sets
from app.services.experts import get_expert_store
from app.services.mcp import list_servers
from app.services.skills import get_skill_store
from app.services.store import get_store

router = APIRouter(tags=["platform"])


class WorkspaceCreate(BaseModel):
    name: str
    description: str = ""


class WorkspacePatch(BaseModel):
    name: str
    description: str | None = None


class ApiKeyCreate(BaseModel):
    name: str
    workspace_id: str | None = None
    scopes: list[str] = Field(default_factory=lambda: ["*"])


def _usage_of(ws_id: str) -> dict:
    store = get_store()
    return {
        "kb": len(store.list_kbs(ws_id)),
        "doc": sum(len(store.documents_of_kb(k.id)) for k in store.list_kbs(ws_id)),
        "skill": len([s for s in get_skill_store().list_skills(ws_id) if not s.builtin]),
        "expert": len(get_expert_store().list_experts(ws_id)),
        "mcp": len(list_servers(ws_id)),
        "eval": len([s for s in list_sets() if store.owns(s.kb_id, ws_id)]),
    }


@router.get("/workspaces")
async def list_workspaces(user: users.User = Depends(current_user)) -> list[dict]:
    """工作区列表按账号可见范围过滤：管理员看全部，成员只看归属。"""
    visible = set(allowed_workspaces(user))
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "created_at": w.created_at,
            "usage": _usage_of(w.id),
        }
        for w in workspaces.get_workspace_store().list()
        if w.id in visible
    ]


@router.post("/workspaces")
async def create_workspace(
    req: WorkspaceCreate, admin: users.User = Depends(current_admin)
) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "工作区名称不能为空")
    store = workspaces.get_workspace_store()
    ws = store.add(req.name.strip(), req.description)
    store.save()
    return {"id": ws.id, "name": ws.name, "description": ws.description,
            "created_at": ws.created_at, "usage": _usage_of(ws.id)}


@router.patch("/workspaces/{ws_id}")
async def patch_workspace(
    ws_id: str, req: WorkspacePatch, admin: users.User = Depends(current_admin)
) -> dict:
    store = workspaces.get_workspace_store()
    ws = store.rename(ws_id, req.name.strip(), req.description)
    if ws is None:
        raise HTTPException(404, "工作区不存在")
    store.save()
    return {"id": ws.id, "name": ws.name, "description": ws.description}


@router.delete("/workspaces/{ws_id}")
async def delete_workspace(
    ws_id: str, admin: users.User = Depends(current_admin)
) -> dict:
    """只允许删除空工作区，避免误删带走数据。"""
    usage = _usage_of(ws_id)
    if sum(usage.values()) > 0:
        raise HTTPException(400, f"工作区非空，请先清理资源：{usage}")
    store = workspaces.get_workspace_store()
    if not store.delete(ws_id):
        raise HTTPException(400, "工作区不存在或为默认工作区（不可删除）")
    store.save()
    return {"ok": True}


# ---------- API Key ----------


@router.get("/apikeys")
async def list_api_keys(ws: str = Depends(current_workspace)) -> list[dict]:
    return [k.to_public() for k in apikeys.get_api_key_store().list_keys(ws)]


@router.post("/apikeys")
async def issue_api_key(
    req: ApiKeyCreate,
    ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    target = req.workspace_id or ws
    if workspaces.get_workspace_store().get(target) is None:
        raise HTTPException(404, "工作区不存在")
    if not user.can_access(target):
        raise HTTPException(403, "无权为该工作区签发密钥")
    store = apikeys.get_api_key_store()
    key, token = store.issue(req.name.strip() or "未命名密钥", target, req.scopes)
    store.save()
    # 明文只在此刻返回一次
    return {"key": key.to_public(), "token": token}


@router.delete("/apikeys/{key_id}")
async def revoke_api_key(key_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = apikeys.get_api_key_store()
    key = store.get(key_id)
    if key is None or key.workspace_id != ws:
        raise HTTPException(404, "密钥不存在")
    store.revoke(key_id)
    store.save()
    return {"ok": True}


# ---------- 审计 ----------


@router.get("/audit")
async def list_audit(limit: int = 100, ws: str = Depends(current_workspace)) -> dict:
    return {
        "summary": audit.summary(ws),
        "entries": audit.list_entries(limit=limit, workspace_id=ws),
    }
