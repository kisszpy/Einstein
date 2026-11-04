"""可观测性查询端点。"""

from fastapi import APIRouter, Query

from app.services import metrics

router = APIRouter(prefix="/obs", tags=["observability"])


@router.get("/summary")
async def get_summary() -> dict:
    return metrics.summary()


@router.get("/requests")
async def get_requests(limit: int = Query(50, le=500)) -> list[dict]:
    return metrics.recent_requests(limit)


@router.get("/llm")
async def get_llm(limit: int = Query(50, le=500)) -> list[dict]:
    return metrics.recent_llm(limit)


@router.delete("/")
async def clear_metrics() -> dict:
    metrics.clear()
    return {"ok": True}
