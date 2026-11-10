"""分享管理（登录态）：发链接、看列表、撤销。

匿名访问的入口在 `routes/public.py`，两个文件刻意分开——那边一个鉴权依赖
都不挂，混在这里迟早有人加错。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_uid, current_workspace
from app.services import kb_access, shares
from app.services.history import get_history_store
from app.services.store import get_store

router = APIRouter(prefix="/shares", tags=["shares"])


class ShareCreate(BaseModel):
    kind: str                      # conversation | kb
    target_id: str
    title: str = ""
    allow_ask: bool = False
    expires_days: int = 0
    note: str = ""


def _assert_target(kind: str, target_id: str, ws: str, uid: str) -> str:
    """确认"这是你能分享的东西"，顺便取回一个像样的默认标题。

    分享等于把内容搬出鉴权边界，所以这里宁可严格：会话必须本人创建，
    知识库至少要能读；共享空间里的公共库不做拦截（能读就有权转给外部）。
    """
    if kind == "conversation":
        conv = get_history_store().get_conversation(target_id)
        if conv is None or conv.workspace_id != ws or conv.owner_id != uid:
            raise HTTPException(404, "会话不存在")
        return conv.title or "未命名会话"
    if kind == "kb":
        if kb_access.access(get_store(), target_id, ws, uid) is None:
            raise HTTPException(404, "知识库不存在")
        kb = get_store().get_kb(target_id)
        return (kb.name if kb else "") or "知识库"
    raise HTTPException(400, "kind 只支持 conversation / kb")


@router.get("")
def list_shares(ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> list[dict]:
    return [shares.payload(s) for s in shares.list_shares(ws, uid)]


@router.post("")
def create_share(
    req: ShareCreate, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    title = _assert_target(req.kind, req.target_id, ws, uid)
    try:
        entry = shares.create_share(
            workspace_id=ws, owner_id=uid, kind=req.kind, target_id=req.target_id,
            title=req.title.strip() or title, allow_ask=req.allow_ask,
            expires_days=req.expires_days, note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return shares.payload(entry)


@router.delete("/{token}")
def revoke_share(
    token: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    entry = shares.revoke(token, workspace_id=ws, owner_id=uid)
    if entry is None:
        raise HTTPException(404, "分享不存在")
    return shares.payload(entry)
