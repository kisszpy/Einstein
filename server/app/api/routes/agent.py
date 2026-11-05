import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.agent import chunk_text, run_agent
from app.services.history import get_history_store
from app.services.store import get_store
from app.services.tools import list_tools

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    task: str
    tool_ids: list[str] = []
    kb_ids: list[str] = []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/tools")
async def get_tools(ws: str = Depends(current_workspace)) -> list[dict]:
    return list_tools(ws)


@router.get("/runs")
async def list_runs() -> list[dict]:
    history = get_history_store()
    return [
        {
            "id": r.id,
            "task": r.task,
            "tool_ids": r.tool_ids,
            "steps": r.steps,
            "answer": r.answer,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in history.list_agent_runs()
    ]


async def _event_stream(req: AgentRunRequest, ws: str) -> AsyncIterator[str]:
    run_id = uuid.uuid4().hex
    yield _sse("meta", {"run_id": run_id})
    # 只允许检索当前工作区的知识库
    kb_ids = get_store().owned_kb_ids(ws, req.kb_ids)
    try:
        final_text = ""
        steps: list[dict] = []
        async for step in run_agent(req.task, req.tool_ids, kb_ids, ws):
            if step["kind"] == "final":
                final_text = step["text"]
            else:
                steps.append(step)
                yield _sse("step", {"step": step})

        yield _sse("answer_start", {})
        for piece in chunk_text(final_text):
            yield _sse("delta", {"text": piece})
        yield _sse("done", {"run_id": run_id})

        # 持久化运行历史
        history = get_history_store()
        history.add_agent_run(
            task=req.task,
            tool_ids=req.tool_ids,
            steps=steps,
            answer=final_text,
            status="ok",
        )
        history.save()
    except Exception as exc:  # noqa: BLE001 — 统一转 SSE error 事件
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


@router.post("/run")
async def run(req: AgentRunRequest, ws: str = Depends(current_workspace)) -> StreamingResponse:
    """SSE 运行智能体。事件：meta -> step* -> answer_start -> delta* -> done | error。"""
    return StreamingResponse(
        _event_stream(req, ws),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
