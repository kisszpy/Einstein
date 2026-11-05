"""技能管理路由。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.skills import Skill, get_skill_store

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = []


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    enabled: bool | None = None


def _dump(skill) -> dict:  # noqa: ANN001 — dataclass，内部使用
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "system_prompt": skill.system_prompt,
        "tools": skill.tools,
        "builtin": skill.builtin,
        "enabled": skill.enabled,
        "workspace_id": skill.workspace_id,
    }


def _require_own(skill: Skill | None, ws: str) -> Skill:
    """内置技能全员可改；自定义技能只能由所属工作区操作。"""
    if skill is None or (not skill.builtin and skill.workspace_id != ws):
        raise HTTPException(404, "技能不存在")
    return skill


@router.get("")
async def list_skills(ws: str = Depends(current_workspace)) -> list[dict]:
    return [_dump(s) for s in get_skill_store().list_skills(ws)]


@router.post("")
async def create_skill(req: SkillCreate, ws: str = Depends(current_workspace)) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "技能名称不能为空")
    store = get_skill_store()
    skill = store.add(req.name.strip(), req.description, req.system_prompt, req.tools, workspace_id=ws)
    store.save()
    return _dump(skill)


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str, req: SkillUpdate, ws: str = Depends(current_workspace)
) -> dict:
    store = get_skill_store()
    _require_own(store.get(skill_id), ws)
    skill = store.update(
        skill_id,
        name=req.name.strip() if req.name else None,
        description=req.description,
        system_prompt=req.system_prompt,
        tools=req.tools,
        enabled=req.enabled,
    )
    if skill is None:
        raise HTTPException(404, "技能不存在")
    store.save()
    return _dump(skill)


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = get_skill_store()
    _require_own(store.get(skill_id), ws)
    if not store.delete(skill_id):
        raise HTTPException(400, "技能不存在或为内置技能（不可删除）")
    store.save()
    return {"ok": True}
