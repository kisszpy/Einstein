"""平台管理：工作区、API Key、审计日志。

控制台使用，走 X-Workspace 上下文；工作区列表本身是全局的（用于切换器）。
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import (
    allowed_workspaces,
    current_admin,
    current_user,
    current_workspace,
    workspace_role,
)
from app.services import apikeys, audit, members, users, workspaces
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


def _usage_of(ws_id: str, uid: str | None = None) -> dict:
    """空间资源用量。

    uid 给定时按「这个账号看得见多少」统计（切换器展示用）；不给就是整个
    空间的真实占用（删除前的非空校验必须用这个，否则会把别人的资源当空）。
    """
    store = get_store()
    kbs = store.list_kbs(ws_id, uid)
    return {
        "kb": len(kbs),
        "doc": sum(len(store.documents_of_kb(k.id)) for k in kbs),
        "skill": len([s for s in get_skill_store().list_skills(ws_id, uid) if not s.builtin]),
        "expert": len(get_expert_store().list_experts(ws_id, uid)),
        "mcp": len(list_servers(ws_id, uid)),
        "eval": len([s for s in list_sets() if store.owns(s.kb_id, ws_id, uid)]),
    }


@router.get("/workspaces")
def list_workspaces(user: users.User = Depends(current_user)) -> list[dict]:
    """工作区列表按成员关系过滤，并带上「我在这个空间的角色」与个人空间标记。"""
    visible = set(allowed_workspaces(user))
    personal = members.get_member_store().personal_of(user.id)
    personal_id = personal.workspace_id if personal else ""
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "created_at": w.created_at,
            "role": workspace_role(user, w.id) or "",
            "is_personal": w.id == personal_id,
            "member_count": len(members.get_member_store().members_of(w.id)),
            "usage": _usage_of(w.id, user.id),
        }
        for w in workspaces.get_workspace_store().list()
        if w.id in visible
    ]


@router.post("/workspaces")
def create_workspace(
    req: WorkspaceCreate,
    admin: users.User = Depends(current_admin),
    ws_ctx: str = Depends(current_workspace),
) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "工作区名称不能为空")
    store = workspaces.get_workspace_store()
    ws = store.add(req.name.strip(), req.description)
    store.save()
    # 创建者即该空间 owner：空间内的成员/邀请/设置由他自己管，不必每次找平台管理员
    members.get_member_store().ensure(admin.id, ws.id, role="owner")
    members.get_member_store().save()
    return {"id": ws.id, "name": ws.name, "description": ws.description,
            "created_at": ws.created_at, "role": "owner", "is_personal": False,
            "member_count": 1, "usage": _usage_of(ws.id, admin.id)}


class InviteCreate(BaseModel):
    role: str = "member"       # member | viewer（邀请不发管理员）
    max_uses: int = 10


def _guard_ws(ws_id: str, user: users.User, minimum: str = "admin") -> str:
    """路径里的空间 + 成员角色校验：团队空间的 admin 只能管自己这个空间。"""
    if workspaces.get_workspace_store().get(ws_id) is None:
        raise HTTPException(404, "工作区不存在")
    role = workspace_role(user, ws_id)
    if not members.at_least(role, minimum):
        raise HTTPException(
            403, f"该操作需要空间 {minimum} 及以上角色（当前：{role or '非成员'}）"
        )
    return role


@router.post("/workspaces/{ws_id}/invites")
def create_invite(
    ws_id: str, req: InviteCreate, user: users.User = Depends(current_user)
) -> dict:
    """生成邀请码：新用户或存量用户凭码加入工作区（角色即码上的角色）。"""
    _guard_ws(ws_id, user, "admin")
    if members.is_personal_workspace(ws_id):
        raise HTTPException(400, "个人空间是私有边界，不能生成邀请码")
    try:
        return workspaces.create_invite(ws_id, req.role, max(1, req.max_uses))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workspaces/{ws_id}/invites")
def list_invites(
    ws_id: str, user: users.User = Depends(current_user)
) -> list[dict]:
    _guard_ws(ws_id, user, "admin")
    return workspaces.list_invites(ws_id)


class MemberPatch(BaseModel):
    role: str


@router.get("/workspaces/{ws_id}/members")
def list_ws_members(
    ws_id: str, user: users.User = Depends(current_user)
) -> list[dict]:
    """空间成员清单（成员即可见，便于知道同事是谁）；角色变更需 admin。"""
    _guard_ws(ws_id, user, "member")
    store = users.get_user_store()
    out: list[dict] = []
    for item in members.get_member_store().members_of(ws_id):
        account = store.get(item.user_id)
        if account is None:
            continue
        out.append(
            {
                "user_id": item.user_id,
                "username": account.username,
                "display_name": account.display_name,
                "role": item.role,
                "is_personal": item.is_personal,
                "joined_at": item.joined_at,
                "disabled": account.disabled,
            }
        )
    out.sort(key=lambda m: (-members.rank(m["role"]), m["username"].lower()))
    return out


@router.patch("/workspaces/{ws_id}/members/{user_id}")
def set_ws_member_role(
    ws_id: str, user_id: str, req: MemberPatch, user: users.User = Depends(current_user)
) -> dict:
    actor = _guard_ws(ws_id, user, "admin")
    if req.role not in (*members.MEMBER_ROLES, "owner"):
        raise HTTPException(400, f"角色只能是 {(*members.MEMBER_ROLES, 'owner')}")
    target = members.get_member_store()
    current = target.role_of(user_id, ws_id)
    if current is None:
        raise HTTPException(404, "该账号不是此空间成员")
    if current == "owner" and req.role != "owner" and actor != "owner":
        raise HTTPException(403, "只有空间所有者可以转移或降级所有者")
    if user_id == user.id and req.role != current:
        raise HTTPException(400, "不能修改自己在该空间内的角色")
    if req.role == "owner" and current != "owner":
        # 转让所有权：原 owner 降为 admin，保证同一空间只有一个 owner
        for item in target.members_of(ws_id):
            if item.role == "owner" and item.user_id != user_id:
                target.set_role(item.user_id, ws_id, "admin")
    target.set_role(user_id, ws_id, req.role)
    target.save()
    audit.record(
        workspace_id=ws_id, key_id=None, key_name=user.username,
        method="PATCH", path=f"/api/workspaces/{ws_id}/members/{user_id}", status=200,
        duration_ms=0, detail=f"{current} -> {req.role}",
    )
    return {"ok": True, "user_id": user_id, "role": req.role}


@router.delete("/workspaces/{ws_id}/members/{user_id}")
def remove_ws_member(
    ws_id: str, user_id: str, user: users.User = Depends(current_user)
) -> dict:
    actor = _guard_ws(ws_id, user, "admin")
    target = members.get_member_store()
    current = target.role_of(user_id, ws_id)
    if current is None:
        raise HTTPException(404, "该账号不是此空间成员")
    if current == "owner":
        raise HTTPException(400, "空间所有者不可被移除，请先转让所有权")
    if user_id == user.id and actor != "owner":
        raise HTTPException(400, "不能移除自己，请使用「退出空间」")
    target.remove(user_id, ws_id)
    target.save()
    # 归属只在成员表：删完关系把内存投影刷新一下即可，users 表没有归属列
    members.sync_user(users.get_user_store().get(user_id))
    audit.record(
        workspace_id=ws_id, key_id=None, key_name=user.username,
        method="DELETE", path=f"/api/workspaces/{ws_id}/members/{user_id}", status=200,
        duration_ms=0, detail=f"remove {current}",
    )
    return {"ok": True}


@router.patch("/workspaces/{ws_id}")
def patch_workspace(
    ws_id: str, req: WorkspacePatch, user: users.User = Depends(current_user)
) -> dict:
    _guard_ws(ws_id, user, "admin")
    store = workspaces.get_workspace_store()
    ws = store.rename(ws_id, req.name.strip(), req.description)
    if ws is None:
        raise HTTPException(404, "工作区不存在")
    store.save()
    return {"id": ws.id, "name": ws.name, "description": ws.description}


@router.delete("/workspaces/{ws_id}")
def delete_workspace(
    ws_id: str, user: users.User = Depends(current_user)
) -> dict:
    """只允许删除空工作区；个人空间不可删，避免把账号的落脚点删掉。"""
    _guard_ws(ws_id, user, "owner")
    if members.is_personal_workspace(ws_id):
        raise HTTPException(400, "个人空间不可删除")
    usage = _usage_of(ws_id)
    if sum(usage.values()) > 0:
        raise HTTPException(400, f"工作区非空，请先清理资源：{usage}")
    store = workspaces.get_workspace_store()
    if not store.delete(ws_id):
        raise HTTPException(400, "工作区不存在或为默认工作区（不可删除）")
    store.save()
    members.get_member_store().drop_workspace(ws_id)
    members.get_member_store().save()
    return {"ok": True}


# ---------- API Key ----------


@router.get("/apikeys")
def list_api_keys(ws: str = Depends(current_workspace)) -> list[dict]:
    return [k.to_public() for k in apikeys.get_api_key_store().list_keys(ws)]


@router.post("/apikeys")
def issue_api_key(
    req: ApiKeyCreate,
    ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    target = req.workspace_id or ws
    if workspaces.get_workspace_store().get(target) is None:
        raise HTTPException(404, "工作区不存在")
    if not members.at_least(workspace_role(user, target), "admin"):
        raise HTTPException(403, "无权为该工作区签发密钥（需要该空间 admin）")
    store = apikeys.get_api_key_store()
    key, token = store.issue(req.name.strip() or "未命名密钥", target, req.scopes, user.id)
    store.save()
    # 明文只在此刻返回一次
    return {"key": key.to_public(), "token": token}


@router.delete("/apikeys/{key_id}")
def revoke_api_key(key_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = apikeys.get_api_key_store()
    key = store.get(key_id)
    if key is None or key.workspace_id != ws:
        raise HTTPException(404, "密钥不存在")
    store.revoke(key_id)
    store.save()
    return {"ok": True}


# ---------- 审计 ----------


@router.get("/audit")
def list_audit(
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
def export_audit(
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
