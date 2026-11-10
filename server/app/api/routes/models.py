"""模型注册表管理：登记端点、设默认、连通性检测。

可见性/可编辑性沿用 MCP 连接那一套：`owner_id=''` 是平台预置（全员可见、
仅管理员可改），其余只看本人。检测是**真发一次请求**——配置写对了不等于
端点活着，而"保存成功但对话一直超时"是最难排查的一类故障。
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

import app.services.models as models_service
from app.api.deps import current_uid, current_user, current_workspace
from app.services import users
from app.services.mcp import decrypt_value

router = APIRouter(prefix="/models", tags=["models"])

PROBE_TIMEOUT = 25  # 本地 27B 冷启动慢，给足余量；超过就当作不可用


class ModelCreate(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str = ""
    context_window: int = 0
    max_output: int = 0
    temperature: float = Field(default=0.7, ge=0, le=2)
    kind: str = "chat"
    is_default: bool = False
    tags: list[str] = Field(default_factory=list)
    note: str = ""
    # 只允许管理员登记"平台预置"模型（owner_id=''），普通用户只能建自己的
    platform: bool = False


class ModelPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None      # 空/None = 不改；要清空传 clear_key
    clear_key: bool = False
    context_window: int | None = None
    max_output: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    kind: str | None = None
    is_default: bool | None = None
    enabled: bool | None = None
    tags: list[str] | None = None
    note: str | None = None


def _require(model_id: str, ws: str, uid: str) -> dict:
    model = models_service.visible_model(model_id, ws, uid)
    if model is None:
        raise HTTPException(404, "模型不存在")
    return model


@router.get("")
def list_models(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    items = [models_service.model_payload(m) for m in models_service.list_models(ws, uid)]
    return {
        "items": items,
        # `.env` 那一档作为唯一只读项返回：页面要能显示"现在实际在用哪个"，
        # 否则一个从没建过模型的部署会看到空列表，误以为没配置。
        "env": models_service.env_payload(),
    }


@router.post("")
def create_model(
    req: ModelCreate,
    ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    if req.platform and not user.is_admin:
        raise HTTPException(403, "只有管理员可以登记平台预置模型")
    try:
        model = models_service.create_model(
            name=req.name,
            base_url=req.base_url,
            model=req.model,
            api_key=req.api_key,
            workspace_id=ws,
            owner_id="" if req.platform else user.id,
            context_window=req.context_window,
            max_output=req.max_output,
            temperature=req.temperature,
            kind=req.kind,
            is_default=req.is_default,
            tags=req.tags,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return models_service.model_payload(model)


@router.patch("/{model_id}")
def update_model(
    model_id: str,
    req: ModelPatch,
    ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    model = _require(model_id, ws, user.id)
    if not models_service.can_edit(model, user.id, user.is_admin):
        raise HTTPException(403, "预置模型只有管理员可以修改")
    patch = req.model_dump(exclude_none=True)
    try:
        updated = models_service.update_model(model_id, patch)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "模型不存在")
    return models_service.model_payload(updated)


@router.delete("/{model_id}")
def delete_model(
    model_id: str,
    ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    model = _require(model_id, ws, user.id)
    if not models_service.can_edit(model, user.id, user.is_admin):
        raise HTTPException(403, "预置模型只有管理员可以删除")
    if not models_service.delete_model(model_id):
        raise HTTPException(404, "模型不存在")
    # 引用它的专家回到「跟随默认」，这里顺带告知影响面
    return {"ok": True, "detached_experts": True}


@router.post("/{model_id}/test")
async def test_model(
    model_id: str,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """真发一次最小请求验证端点可用，结果落库供列表展示。"""
    model = _require(model_id, ws, uid)
    encrypted = model.get("api_key", "")
    api_key = decrypt_value(encrypted) if encrypted else ""
    if not api_key:
        from app.core.config import get_settings

        api_key = get_settings().llm_api_key

    started = time.perf_counter()
    error = ""
    reply = ""
    try:
        client = AsyncOpenAI(
            base_url=model.get("base_url", ""), api_key=api_key or "not-needed",
            timeout=PROBE_TIMEOUT, max_retries=0,
        )
        try:
            resp = await client.chat.completions.create(
                model=model.get("model", ""),
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
                temperature=0,
            )
            reply = (resp.choices[0].message.content or "").strip()[:80]
        finally:
            await client.close()
    except Exception as exc:  # noqa: BLE001 — 任何异常都算不可用，原因如实回显
        error = f"{type(exc).__name__}: {exc}"[:500]
    elapsed = round((time.perf_counter() - started) * 1000)

    updated = models_service.record_probe(model_id, ok=not error, error=error)
    return {
        "ok": not error,
        "latency_ms": elapsed,
        "error": error,
        "reply": reply,
        "model": models_service.model_payload(updated or model),
    }
