"""后台任务查询接口。

队列本身由独立 worker 进程消费，这里只做可观测：
前端/运维能看任务列表、失败原因，并对失败任务手动重试。
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import current_workspace
from app.services.queue import get_queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/stats")
async def job_stats(ws: str = Depends(current_workspace)) -> dict:
    return get_queue().stats(ws)


@router.get("")
async def list_jobs(
    status: str | None = None, limit: int = 50, ws: str = Depends(current_workspace),
) -> list[dict]:
    return [job.to_dict() for job in get_queue().list(status=status, workspace_id=ws, limit=limit)]


def _require_own(job_id: str, ws: str):  # noqa: ANN202
    job = get_queue().get(job_id)
    if job is None or job.workspace_id != ws:
        raise HTTPException(404, "任务不存在")
    return job


@router.get("/{job_id}")
async def get_job(job_id: str, ws: str = Depends(current_workspace)) -> dict:
    return _require_own(job_id, ws).to_dict()


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, ws: str = Depends(current_workspace)) -> dict:
    _require_own(job_id, ws)
    job = get_queue().retry(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job.to_dict()
