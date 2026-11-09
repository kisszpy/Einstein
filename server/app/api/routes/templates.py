"""任务模板：把固定流程存成可复用起点。

发起任务时把模板里的 `prompt / kb_ids / tool_ids / expert_id / project`
直接灌进 `POST /api/agent/run`，执行仍由智能体自主规划。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import app.services.templates as templates
from app.api.deps import current_uid, current_user, current_workspace
from app.services import users

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    prompt: str
    description: str = ""
    expert_id: str = ""
    project: str = ""
    tool_ids: list[str] = Field(default_factory=list)
    kb_ids: list[str] = Field(default_factory=list)
    visibility: str = "private"


class TemplatePatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    description: str | None = None
    expert_id: str | None = None
    project: str | None = None
    tool_ids: list[str] | None = None
    kb_ids: list[str] | None = None
    visibility: str | None = None


def _require_visible(template_id: str, ws: str, uid: str) -> dict:
    tpl = templates.get_template(template_id)
    if tpl is None or not templates.visible(tpl, ws, uid):
        raise HTTPException(404, "任务模板不存在")
    return tpl


@router.get("")
async def list_templates(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> list[dict]:
    return [templates.payload(t) for t in templates.list_templates(ws, uid)]


@router.post("")
async def create_template(
    req: TemplateCreate,
    ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    try:
        tpl = templates.create_template(
            workspace_id=ws, owner_id=user.id, name=req.name, prompt=req.prompt,
            description=req.description, expert_id=req.expert_id, project=req.project,
            tool_ids=req.tool_ids, kb_ids=req.kb_ids, visibility=req.visibility,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return templates.payload(tpl)


@router.patch("/{template_id}")
async def update_template(
    template_id: str, req: TemplatePatch,
    ws: str = Depends(current_workspace), user: users.User = Depends(current_user),
) -> dict:
    tpl = _require_visible(template_id, ws, user.id)
    if not templates.can_edit(tpl, user.id, user.is_admin):
        raise HTTPException(403, "只有创建者或管理员可以修改这个模板")
    try:
        updated = templates.update_template(template_id, req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "任务模板不存在")
    return templates.payload(updated)


@router.delete("/{template_id}")
async def delete_template(
    template_id: str, ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    tpl = _require_visible(template_id, ws, user.id)
    if not templates.can_edit(tpl, user.id, user.is_admin):
        raise HTTPException(403, "只有创建者或管理员可以删除这个模板")
    return {"ok": templates.delete_template(template_id)}


@router.post("/{template_id}/use")
async def use_template(
    template_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    """标记一次使用并返回完整参数，前端据此填充发起任务的表单。"""
    _require_visible(template_id, ws, uid)
    tpl = templates.touch(template_id)
    return templates.payload(tpl or {})
