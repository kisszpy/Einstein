"""评测集管理与运行。

评测集通过所挂的知识库间接归属工作区：只能看到/操作自己工作区内知识库上的评测集。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_uid, current_workspace
from app.services import eval as eval_service
from app.services.store import get_store

router = APIRouter(prefix="/eval", tags=["eval"])


class EvalItemIn(BaseModel):
    query: str
    expected_doc: str = ""
    expected_contains: list[str] = Field(default_factory=list)
    expected_text: str = ""


class EvalSetIn(BaseModel):
    kb_id: str
    name: str
    items: list[EvalItemIn]


def _visible_ids(ws: str, uid: str) -> set[str]:
    """当前用户可见知识库上的评测集 ID 集合（评测集跟着知识库归属走）。"""
    store = get_store()
    return {s.id for s in eval_service.list_sets() if store.owns(s.kb_id, ws, uid)}


def _require_visible(set_id: str, ws: str, uid: str) -> None:
    if set_id not in _visible_ids(ws, uid):
        raise HTTPException(404, "评测集不存在")


@router.get("/sets")
async def list_sets(kb_id: str | None = None, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> list[dict]:
    visible = _visible_ids(ws, uid)
    return [
        eval_service.set_payload(s)
        for s in eval_service.list_sets(kb_id)
        if s.id in visible
    ]


@router.post("/sets")
async def add_set(req: EvalSetIn, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    if not req.items:
        raise HTTPException(400, "评测集至少需要一条用例")
    if not get_store().owns(req.kb_id, ws, uid):
        raise HTTPException(404, "知识库不存在")
    es = eval_service.add_set(
        kb_id=req.kb_id,
        name=req.name,
        items=[eval_service.EvalItem(**i.model_dump()) for i in req.items],
    )
    return eval_service.set_payload(es)


@router.delete("/sets/{set_id}")
async def delete_set(set_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    _require_visible(set_id, ws, uid)
    if not eval_service.remove_set(set_id):
        raise HTTPException(404, "评测集不存在")
    return {"ok": True}


@router.post("/sets/{set_id}/run")
async def run(
    set_id: str, mode: str | None = None, judge: bool = False,
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    """mode 可选：vector / lexical / hybrid；judge=true 开 LLM 忠实性裁判。"""
    _require_visible(set_id, ws, uid)
    try:
        report = await eval_service.run_eval(set_id, mode=mode, judge=judge)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    if report is None:
        raise HTTPException(404, "评测集不存在")
    return report


@router.get("/sets/{set_id}/runs")
async def run_history(set_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> list[dict]:
    """历史运行（新→旧，不含逐条明细），两次运行的指标对比在前端做。"""
    _require_visible(set_id, ws, uid)
    return eval_service.list_runs(set_id)


@router.get("/sets/{set_id}/runs/{run_id}")
async def run_detail(set_id: str, run_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    """单次运行全量报告（含逐条 items）。"""
    _require_visible(set_id, ws, uid)
    report = eval_service.get_run(set_id, run_id)
    if report is None:
        raise HTTPException(404, "运行记录不存在")
    return report
