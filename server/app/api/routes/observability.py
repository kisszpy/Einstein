"""可观测性查询端点。"""

from fastapi import APIRouter, Query

from app.services import checkpoints, metrics

router = APIRouter(prefix="/obs", tags=["observability"])


@router.get("/summary")
async def get_summary() -> dict:
    return metrics.summary()


@router.get("/checkpoints")
async def get_checkpoints() -> dict:
    """短期记忆的落点：档位、目标、TTL、是否已连上。

    排查「批准后没有续跑而是重跑了」时先看这里——`connected=false` 说明
    检查点没落盘（配置成了 memory 或连不上，日志里有降级原因）。
    """
    return checkpoints.describe()


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
