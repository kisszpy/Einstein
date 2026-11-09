from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.api.deps import current_uid, current_workspace
from app.services import transcript
from app.services.history import get_history_store

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _require_own(conv_id: str, ws: str, uid: str):
    """会话是私有数据：空间不对或不是本人创建，一律 404（不暴露存在性）。"""
    conv = get_history_store().get_conversation(conv_id)
    if conv is None or conv.workspace_id != ws or conv.owner_id != uid:
        raise HTTPException(404, "会话不存在")
    return conv


@router.get("")
async def list_conversations(
    q: str | None = None, archived: bool = False,
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> list[dict]:
    """q 为空列出未归档会话；给了 q 就全文搜索（标题+消息正文）。"""
    history = get_history_store()
    convs = (history.search_conversations(ws, q, include_archived=archived, owner_id=uid)
             if q else history.list_conversations(ws, uid))
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
    uid: str = Depends(current_uid),
) -> dict:
    """重命名 / 归档 / 置顶，字段可选只改传了的。"""
    history = get_history_store()
    _require_own(conversation_id, ws, uid)
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
async def get_conversation(
    conversation_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    conv = _require_own(conversation_id, ws, uid)
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in conv.messages
        ],
    }


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: str, format: str = "md", inline: bool = False,
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> Response:
    """导出整段会话：`format=md`（默认，给人看）/ `json`（给脚本处理）。

    `inline=true` 时用 inline 而非 attachment——分享面板里想直接预览时用，
    默认 attachment 让浏览器走"另存为"，避免把 Markdown 当网页打开。
    """
    conv = _require_own(conversation_id, ws, uid)
    picked = transcript.RENDERERS.get((format or "md").lower())
    if picked is None:
        raise HTTPException(400, "format 只支持 md / json")
    ext, media_type, render = picked
    body = render(conv)
    disp = transcript.disposition(conv.title, ext)
    if inline:
        disp = disp.replace("attachment;", "inline;", 1)
    return Response(content=body, media_type=media_type, headers={"Content-Disposition": disp})


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    _require_own(conversation_id, ws, uid)
    get_history_store().delete_conversation(conversation_id)
    return {"deleted": conversation_id}
