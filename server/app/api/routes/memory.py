"""反馈与长期记忆 API。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import current_user, current_workspace
from app.services import memory as memory_service, users

router = APIRouter(tags=["memory"])


class FeedbackIn(BaseModel):
    conversation_id: str = ""
    session_id: str = ""
    turn: int = 1
    rating: str          # up | down
    reason: str = ""


@router.post("/feedback")
def add_feedback(
    req: FeedbackIn, ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    try:
        return memory_service.add_feedback(
            workspace_id=ws, session_id=req.session_id,
            conversation_id=req.conversation_id, turn=req.turn,
            rating=req.rating, reason=req.reason, user=user.username,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/feedback")
def list_feedback(
    rating: str | None = None, limit: int = 100,
    ws: str = Depends(current_workspace),
) -> dict:
    if rating is not None and rating not in ("up", "down"):
        raise HTTPException(400, "rating 只能是 up / down")
    return {
        "summary": memory_service.feedback_summary(ws),
        "entries": memory_service.list_feedback(ws, rating=rating, limit=limit),
    }


class MemoryIn(BaseModel):
    content: str


@router.get("/memory")
def list_memory(
    q: str | None = None, limit: int = 100, ws: str = Depends(current_workspace),
) -> list[dict]:
    return memory_service.list_memory(ws, q=q, limit=limit)


@router.post("/memory")
def add_memory(
    req: MemoryIn, ws: str = Depends(current_workspace),
) -> dict:
    try:
        return memory_service.add_memory(ws, req.content, source="manual")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/memory/{memory_id}")
def remove_memory(memory_id: str, ws: str = Depends(current_workspace)) -> dict:
    ok = memory_service.remove_memory(ws, memory_id)
    if not ok:
        raise HTTPException(404, "记忆不存在")
    return {"ok": True}
