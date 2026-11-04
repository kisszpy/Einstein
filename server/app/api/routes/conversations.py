from fastapi import APIRouter, HTTPException

from app.services.history import get_history_store

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations() -> list[dict]:
    history = get_history_store()
    return [
        {
            "id": c.id,
            "title": c.title,
            "message_count": len(c.messages),
            "updated_at": c.updated_at,
        }
        for c in history.list_conversations()
    ]


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict:
    conv = get_history_store().get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in conv.messages
        ],
    }


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    ok = get_history_store().delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(404, "会话不存在")
    get_history_store().save()
    return {"deleted": conversation_id}
