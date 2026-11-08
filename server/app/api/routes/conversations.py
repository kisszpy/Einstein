from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.history import get_history_store

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _require_own(conv_id: str, ws: str):
    conv = get_history_store().get_conversation(conv_id)
    if conv is None or conv.workspace_id != ws:
        raise HTTPException(404, "会话不存在")
    return conv


@router.get("")
async def list_conversations(
    q: str | None = None, archived: bool = False,
    ws: str = Depends(current_workspace),
) -> list[dict]:
    """q 为空列出未归档会话；给了 q 就全文搜索（标题+消息正文）。"""
    history = get_history_store()
    convs = (history.search_conversations(ws, q, include_archived=archived)
             if q else history.list_conversations(ws))
    if archived and not q:
        convs = [c for c in convs if c.archived]
    elif not q:
        convs = [c for c in convs if not c.archived]
    return [
        {
            "id": c.id,
            "title": c.title,
            "message_count": c.message_count or len(c.messages),
            "updated_at": c.updated_at,
            "archived": c.archived,
            "pinned": c.pinned,
        }
        for c in convs
    ]


class ConversationPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None
    pinned: bool | None = None


@router.patch("/{conversation_id}")
async def patch_conversation(
    conversation_id: str, req: ConversationPatch, ws: str = Depends(current_workspace),
) -> dict:
    """重命名 / 归档 / 置顶，字段可选只改传了的。"""
    history = get_history_store()
    _require_own(conversation_id, ws)
    conv = None
    if req.title is not None:
        if not req.title.strip():
            raise HTTPException(400, "标题不能为空")
        conv = history.rename_conversation(conversation_id, req.title.strip())
    if req.archived is not None or req.pinned is not None:
        # 重命名与归档/置顶可同时生效，不要互斥
        conv = history.set_conversation_flags(
            conversation_id, archived=req.archived, pinned=req.pinned)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    return {"id": conv.id, "title": conv.title, "archived": conv.archived, "pinned": conv.pinned}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, ws: str = Depends(current_workspace)) -> dict:
    conv = _require_own(conversation_id, ws)
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in conv.messages
        ],
    }


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, ws: str = Depends(current_workspace)) -> dict:
    _require_own(conversation_id, ws)
    get_history_store().delete_conversation(conversation_id)
    return {"deleted": conversation_id}
