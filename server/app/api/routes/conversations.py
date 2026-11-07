from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import current_workspace
from app.services.history import get_history_store

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _require_own(conv_id: str, ws: str):
    conv = get_history_store().get_conversation(conv_id)
    if conv is None or conv.workspace_id != ws:
        raise HTTPException(404, "会话不存在")
    return conv


@router.get("")
async def list_conversations(ws: str = Depends(current_workspace)) -> list[dict]:
    history = get_history_store()
    return [
        {
            "id": c.id,
            "title": c.title,
            # 列表接口只取计数，SQL 后端不会为了显示条数把全部消息读出来
            "message_count": c.message_count or len(c.messages),
            "updated_at": c.updated_at,
        }
        for c in history.list_conversations(ws)
    ]


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
