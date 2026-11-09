"""专家管理路由。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_uid, current_user, current_workspace
from app.services.experts import Expert, get_expert_store
from app.services.skills import get_skill_store
from app.services.store import get_store

router = APIRouter(prefix="/experts", tags=["experts"])


class ExpertCreate(BaseModel):
    name: str
    description: str = ""
    emoji: str = "🤖"
    persona: str = ""
    skill_ids: list[str] = []
    kb_ids: list[str] = []
    tool_ids: list[str] = []
    temperature: float = 0.7
    greeting: str = ""
    suggestions: list[str] = []


class ExpertUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    emoji: str | None = None
    persona: str | None = None
    skill_ids: list[str] | None = None
    kb_ids: list[str] | None = None
    tool_ids: list[str] | None = None
    temperature: float | None = None
    enabled: bool | None = None
    greeting: str | None = None
    suggestions: list[str] | None = None


class PublishRequest(BaseModel):
    status: str  # draft | testing | live


def _dump(expert) -> dict:  # noqa: ANN001 — dataclass，内部使用
    return {
        "id": expert.id,
        "name": expert.name,
        "description": expert.description,
        "emoji": expert.emoji,
        "persona": expert.persona,
        "skill_ids": expert.skill_ids,
        "kb_ids": expert.kb_ids,
        "tool_ids": expert.tool_ids,
        "temperature": expert.temperature,
        "enabled": expert.enabled,
        "workspace_id": expert.workspace_id,
        "greeting": expert.greeting,
        "suggestions": expert.suggestions,
        "status": expert.status,
        "published_at": expert.published_at,
    }


def _require_visible(expert: Expert | None, ws: str, uid: str) -> Expert:
    """可见：预置专家（'' ）或自己创建的；别人的一律当不存在。"""
    if expert is None or expert.workspace_id != ws or expert.owner_id not in ("", uid):
        raise HTTPException(404, "专家不存在")
    return expert


def _require_editable(expert: Expert | None, ws: str, uid: str, is_admin: bool) -> Expert:
    """可改：自己的专家；预置专家只有管理员能改。"""
    _require_visible(expert, ws, uid)
    assert expert is not None
    if not get_expert_store().can_edit(expert, uid, is_admin):
        raise HTTPException(403, "预置专家只有管理员可以修改")
    return expert


def _filter_mounts(skill_ids: list[str], kb_ids: list[str], ws: str, uid: str) -> tuple[list[str], list[str]]:
    """挂载只能指向自己可见的技能与知识库，越权的静默丢弃。"""
    skill_store = get_skill_store()
    store = get_store()
    return (
        [sid for sid in skill_ids if skill_store.visible(sid, ws, uid)],
        store.owned_kb_ids(ws, kb_ids, uid),
    )


@router.get("")
async def list_experts(
    published: bool = False, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> list[dict]:
    """published=true 只返回 live 且启用的专家（对话页用）；管理页返回全部。"""
    rows = get_expert_store().list_experts(ws, uid)
    if published:
        rows = [e for e in rows if e.status == "live" and e.enabled]
    return [_dump(e) for e in rows]


@router.get("/{expert_id}")
async def get_expert(
    expert_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    return _dump(_require_visible(get_expert_store().get(expert_id), ws, uid))


@router.post("")
async def create_expert(
    req: ExpertCreate, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "专家名称不能为空")
    store = get_expert_store()
    skill_ids, kb_ids = _filter_mounts(req.skill_ids, req.kb_ids, ws, uid)
    expert = store.add(
        name=req.name.strip(),
        description=req.description,
        emoji=req.emoji or "🤖",
        persona=req.persona,
        skill_ids=skill_ids,
        kb_ids=kb_ids,
        tool_ids=req.tool_ids,
        temperature=req.temperature,
        workspace_id=ws,
        greeting=req.greeting,
        suggestions=req.suggestions,
        owner_id=uid,
    )
    store.save()
    return _dump(expert)


@router.put("/{expert_id}")
async def update_expert(
    expert_id: str, req: ExpertUpdate, ws: str = Depends(current_workspace),
    user=Depends(current_user), uid: str = Depends(current_uid),
) -> dict:
    store = get_expert_store()
    _require_editable(store.get(expert_id), ws, uid, user.is_admin)
    skill_ids, kb_ids = _filter_mounts(req.skill_ids or [], req.kb_ids or [], ws, uid)
    expert = store.update(
        expert_id,
        name=req.name.strip() if req.name else None,
        description=req.description,
        emoji=req.emoji,
        persona=req.persona,
        skill_ids=skill_ids if req.skill_ids is not None else None,
        kb_ids=kb_ids if req.kb_ids is not None else None,
        tool_ids=req.tool_ids,
        temperature=req.temperature,
        enabled=req.enabled,
        greeting=req.greeting,
        suggestions=req.suggestions,
    )
    if expert is None:
        raise HTTPException(404, "专家不存在")
    store.save()
    return _dump(expert)


@router.post("/{expert_id}/publish")
async def publish_expert(
    expert_id: str, req: PublishRequest, ws: str = Depends(current_workspace),
    user=Depends(current_user), uid: str = Depends(current_uid),
) -> dict:
    """发布流：draft → testing → live，可回退。非法转移返回 400。"""
    store = get_expert_store()
    _require_editable(store.get(expert_id), ws, uid, user.is_admin)
    expert = store.publish(expert_id, req.status)
    if expert is None:
        raise HTTPException(400, f"不允许从当前状态转移到 {req.status}")
    store.save()
    return _dump(expert)


@router.delete("/{expert_id}")
async def delete_expert(
    expert_id: str, ws: str = Depends(current_workspace),
    user=Depends(current_user), uid: str = Depends(current_uid),
) -> dict:
    store = get_expert_store()
    _require_editable(store.get(expert_id), ws, uid, user.is_admin)
    store.delete(expert_id)
    store.save()
    return {"ok": True}
