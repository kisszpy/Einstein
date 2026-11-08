"""平台管理：工作区、API Key、审计日志。

控制台使用，走 X-Workspace 上下文；工作区列表本身是全局的（用于切换器）。
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
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


class InviteCreate(BaseModel):
    role: str = "member"       # member | viewer（邀请不发管理员）
    max_uses: int = 10


@router.post("/workspaces/{ws_id}/invites")
async def create_invite(
    ws_id: str, req: InviteCreate, admin: users.User = Depends(current_admin)
) -> dict:
    """生成邀请码：新用户或存量用户凭码加入工作区（角色即码上的角色）。"""
    if workspaces.get_workspace_store().get(ws_id) is None:
        raise HTTPException(404, "工作区不存在")
    try:
        return workspaces.create_invite(ws_id, req.role, max(1, req.max_uses))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workspaces/{ws_id}/invites")
async def list_invites(
    ws_id: str, admin: users.User = Depends(current_admin)
) -> list[dict]:
    return workspaces.list_invites(ws_id)


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
async def list_audit(
    limit: int = 100,
    action: str | None = None,
    user_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    ws: str = Depends(current_workspace),
) -> dict:
    """审计筛选：按路径动作 / 调用方 Key / 时间窗过滤。"""
    entries = audit.list_entries(limit=2000, workspace_id=ws)
    def _match(e: dict) -> bool:
        if action and action.lower() not in str(e.get("path", "")).lower():
            return False
        if user_id and e.get("key_id") != user_id:
            return False
        ts = str(e.get("ts") or "")
        if since and ts < since:
            return False
        if until and ts[: len(until)] > until:
            return False
        return True
    filtered = [e for e in entries if _match(e)]
    return {
        "summary": audit.summary(ws),
        "entries": filtered[: max(1, limit)],
        "total_filtered": len(filtered),
    }


@router.get("/audit/export.csv")
async def export_audit(
    action: str | None = None,
    since: str | None = None,
    until: str | None = None,
    ws: str = Depends(current_workspace),
) -> Response:
    """导出筛选后的审计为 CSV（审计合规场景）。"""
    import csv
    import io

    entries = audit.list_entries(limit=2000, workspace_id=ws)
    def _match(e: dict) -> bool:
        if action and action.lower() not in str(e.get("path", "")).lower():
            return False
        ts = str(e.get("ts") or "")
        if since and ts < since:
            return False
        if until and ts[: len(until)] > until:
            return False
        return True
    rows = [e for e in entries if _match(e)]
    buf = io.StringIO()
    fields = ["ts", "workspace_id", "key_name", "method", "path", "status",
              "duration_ms", "tokens_in", "tokens_out", "detail"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for e in rows:
        writer.writerow({k: e.get(k, "") for k in fields})
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),   # BOM 让 Excel 直接打开不乱码
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=audit-{ws}.csv"},
    )
