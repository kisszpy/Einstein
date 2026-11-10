"""可观测性查询端点。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import current_workspace
from app.services import checkpoints, metrics, traces

router = APIRouter(prefix="/obs", tags=["observability"])


@router.get("/summary")
def get_summary() -> dict:
    return metrics.summary()


@router.get("/checkpoints")
def get_checkpoints() -> dict:
    """短期记忆的落点：档位、目标、TTL、是否已连上。

    排查「批准后没有续跑而是重跑了」时先看这里——`connected=false` 说明
    检查点没落盘（配置成了 memory 或连不上，日志里有降级原因）。
    """
    return checkpoints.describe()


@router.get("/requests")
def get_requests(limit: int = Query(50, le=500)) -> list[dict]:
    return metrics.recent_requests(limit)


@router.get("/llm")
def get_llm(limit: int = Query(50, le=500)) -> list[dict]:
    return metrics.recent_llm(limit)


@router.get("/cost")
def get_cost(
    days: int = Query(7, ge=1, le=90),
    ws: str = Depends(current_workspace),
) -> dict:
    """用量与花费：按天 / 模型 / 成员 / 入口四种分组。

    刻意不拆成四个接口：它们读的是同一批行，拆开就要扫四遍库，而看板上
    这四个表本来就是一起看的。数据来自 `run_traces`，所以只有走过对话或
    智能体的调用才计入——评测这类后台任务的 token 在 metrics 里，不进这张账。
    """
    return traces.usage_summary(workspace_id=ws, days=days)


@router.get("/traces")
def list_traces(
    limit: int = Query(50, le=500),
    kind: str | None = None,
    status: str | None = None,
    ws: str = Depends(current_workspace),
) -> list[dict]:
    """按 run 的运行轨迹。一次问答/一次智能体任务一行，含检索命中与工具耗时。"""
    return traces.list_runs(workspace_id=ws, kind=kind, status=status, limit=limit)


@router.get("/traces/summary")
def traces_summary(ws: str = Depends(current_workspace)) -> dict:
    """近 1000 条运行的健康度：错误率、检索未命中率、平均耗时、token 合计。"""
    return traces.summary(workspace_id=ws)


@router.get("/traces/{run_id}")
def get_trace(run_id: str, ws: str = Depends(current_workspace)) -> dict:
    row = traces.get_run(run_id)
    if row is None or row.get("workspace_id") != ws:
        raise HTTPException(404, "运行记录不存在")
    return row


@router.delete("/")
def clear_metrics() -> dict:
    metrics.clear()
    return {"ok": True}
