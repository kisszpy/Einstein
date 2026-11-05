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
    }


def _require_own(expert: Expert | None, ws: str) -> Expert:
    if expert is None or expert.workspace_id != ws:
        raise HTTPException(404, "专家不存在")
    return expert


@router.get("")
async def list_experts(ws: str = Depends(current_workspace)) -> list[dict]:
    return [_dump(e) for e in get_expert_store().list_experts(ws)]


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
    )
    if expert is None:
        raise HTTPException(404, "专家不存在")
    store.save()
    return _dump(expert)


@router.delete("/{expert_id}")
async def delete_expert(expert_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = get_expert_store()
    _require_own(store.get(expert_id), ws)
    store.delete(expert_id)
    store.save()
    return {"ok": True}
