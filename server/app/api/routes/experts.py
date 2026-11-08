"""专家管理路由。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.experts import Expert, get_expert_store

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


def _require_own(expert: Expert | None, ws: str) -> Expert:
    if expert is None or expert.workspace_id != ws:
        raise HTTPException(404, "专家不存在")
    return expert


@router.get("")
async def list_experts(
    published: bool = False, ws: str = Depends(current_workspace)
) -> list[dict]:
    """published=true 只返回 live 且启用的专家（对话页用）；管理页返回全部。"""
    rows = get_expert_store().list_experts(ws)
    if published:
        rows = [e for e in rows if e.status == "live" and e.enabled]
    return [_dump(e) for e in rows]


@router.get("/{expert_id}")
async def get_expert(expert_id: str, ws: str = Depends(current_workspace)) -> dict:
    return _dump(_require_own(get_expert_store().get(expert_id), ws))


@router.post("")
async def create_expert(req: ExpertCreate, ws: str = Depends(current_workspace)) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "专家名称不能为空")
    store = get_expert_store()
    expert = store.add(
        name=req.name.strip(),
        description=req.description,
        emoji=req.emoji or "🤖",
        persona=req.persona,
        skill_ids=req.skill_ids,
        kb_ids=req.kb_ids,
        tool_ids=req.tool_ids,
        temperature=req.temperature,
        workspace_id=ws,
        greeting=req.greeting,
        suggestions=req.suggestions,
    )
    store.save()
    return _dump(expert)


@router.put("/{expert_id}")
async def update_expert(
    expert_id: str, req: ExpertUpdate, ws: str = Depends(current_workspace)
) -> dict:
    store = get_expert_store()
    _require_own(store.get(expert_id), ws)
    expert = store.update(
        expert_id,
        name=req.name.strip() if req.name else None,
        description=req.description,
        emoji=req.emoji,
        persona=req.persona,
        skill_ids=req.skill_ids,
        kb_ids=req.kb_ids,
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
    expert_id: str, req: PublishRequest, ws: str = Depends(current_workspace)
) -> dict:
    """发布流：draft → testing → live，可回退。非法转移返回 400。"""
    store = get_expert_store()
    _require_own(store.get(expert_id), ws)
    expert = store.publish(expert_id, req.status)
    if expert is None:
        raise HTTPException(400, f"不允许从当前状态转移到 {req.status}")
    store.save()
    return _dump(expert)


@router.delete("/{expert_id}")
async def delete_expert(expert_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = get_expert_store()
    _require_own(store.get(expert_id), ws)
    store.delete(expert_id)
    store.save()
    return {"ok": True}
