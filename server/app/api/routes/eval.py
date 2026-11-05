"""评测集管理与运行。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import eval as eval_service

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


@router.get("/sets")
async def list_sets(kb_id: str | None = None) -> list[dict]:
    return [eval_service.set_payload(s) for s in eval_service.list_sets(kb_id)]


@router.post("/sets")
async def add_set(req: EvalSetIn) -> dict:
    if not req.items:
        raise HTTPException(400, "评测集至少需要一条用例")
    es = eval_service.add_set(
        kb_id=req.kb_id,
        name=req.name,
        items=[eval_service.EvalItem(**i.model_dump()) for i in req.items],
    )
    return eval_service.set_payload(es)


@router.delete("/sets/{set_id}")
async def delete_set(set_id: str) -> dict:
    if not eval_service.remove_set(set_id):
        raise HTTPException(404, "评测集不存在")
    return {"ok": True}


@router.post("/sets/{set_id}/run")
async def run(set_id: str, mode: str | None = None) -> dict:
    """mode 可选：vector / lexical / hybrid，用于对比不同检索策略。"""
    try:
        report = await eval_service.run_eval(set_id, mode=mode)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    if report is None:
        raise HTTPException(404, "评测集不存在")
    return report
