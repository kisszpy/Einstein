import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.agent import chunk_text, run_agent
from app.services.capabilities import AgentContext, sandbox
from app.services.history import get_history_store
from app.services.store import get_store
from app.services.tools import list_tools

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    task: str
    tool_ids: list[str] = []
    kb_ids: list[str] = []
    project: str = ""
    # 追问时带上所属会话 id；为空表示新任务（服务端分配）
    session_id: str = ""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/tools")
async def get_tools(ws: str = Depends(current_workspace)) -> dict:
    return list_tools(ws)


@router.get("/runs")
async def list_runs(ws: str = Depends(current_workspace)) -> list[dict]:
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
            "project": r.project,
            "artifacts": r.artifacts,
            "session_id": history.session_key(r),
            "turn": r.turn,
        }
        for r in history.list_agent_runs(ws)
    ]


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, ws: str = Depends(current_workspace)) -> dict:
    """删除一个任务会话（含所有追问轮次）。参数可以是会话 id 或任一轮次的 run id。"""
    history = get_history_store()
    runs = history.list_session_runs(run_id, ws)
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    deleted = history.delete_session(run_id, ws)
    return {"ok": True, "deleted": deleted}


async def _event_stream(req: AgentRunRequest, ws: str) -> AsyncIterator[str]:
    history = get_history_store()
    # 会话：追问沿用同一会话，新任务分配新 id
    session_id = req.session_id or uuid.uuid4().hex[:12]
    prior_runs = history.list_session_runs(session_id, ws) if req.session_id else []
    run_id = uuid.uuid4().hex
    print(
        f"[agent-stream] {run_id} start session={session_id} turn={len(prior_runs) + 1} task={req.task[:40]!r}",
        flush=True,
    )
    yield _sse("meta", {"run_id": run_id, "session_id": session_id, "turn": len(prior_runs) + 1})
    # 把最近几轮问答带给模型（每轮答案截断，防止本地模型上下文被撑爆）
    lc_history: list[dict] = []
    for r in prior_runs[-3:]:
        lc_history.append({"role": "user", "content": r.task})
        if r.answer:
            lc_history.append({"role": "assistant", "content": r.answer[:1500]})
    try:
        # 只允许检索当前工作区的知识库；项目名给定时必须已存在
        kb_ids = get_store().owned_kb_ids(ws, req.kb_ids)

        ctx = AgentContext(workspace_id=ws, project=req.project)
        if ctx.project:
            try:
                sandbox.project_dir(ws, ctx.project)
            except sandbox.SandboxError as exc:
                yield _sse("error", {"message": str(exc)})
                return
    except Exception as exc:  # noqa: BLE001 — 建流阶段失败也要回 error 事件，不能静默断流
        print(f"[agent-stream] {run_id} setup-error: {type(exc).__name__}: {exc}", flush=True)
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
        return

    try:
        final_text = ""
        steps: list[dict] = []
        artifacts: list[dict] = []
        # 心跳泵：大模型思考期间流上不能长时间无数据，否则连接会被
        # 空闲超时/中间层掐断（客户端只收到 meta 就 EOF）。
        # 用独立任务把事件搬进队列，消费端超时只发注释帧，绝不取消生产者。
        queue: asyncio.Queue[Any] = asyncio.Queue()
        _STOP = object()

        async def _pump() -> None:
            try:
                async for step in run_agent(
                    req.task, req.tool_ids, kb_ids, ws, ctx, history=lc_history
                ):
                    await queue.put(step)
            except Exception as exc:  # noqa: BLE001 — 异常也走队列，保持事件序
                await queue.put(exc)
            finally:
                await queue.put(_STOP)

        pump = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is _STOP:
                    break
                if isinstance(item, Exception):
                    raise item
                step = item
                kind = step["kind"]
                if kind == "final":
                    final_text = step["text"]
                elif kind == "artifact":
                    entry = {"path": step.get("path", ""), "kind": step.get("artifact_kind", "file")}
                    if entry not in artifacts:
                        artifacts.append(entry)
                        yield _sse("artifact", {"artifact": entry})
                else:
                    steps.append(step)
                    yield _sse("step", {"step": step})
        finally:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        yield _sse("answer_start", {})
        for piece in chunk_text(final_text):
            yield _sse("delta", {"text": piece})
        yield _sse(
            "done",
            {
                "run_id": run_id,
                "session_id": session_id,
                "project": ctx.project,
                "artifacts": artifacts,
            },
        )
        print(f"[agent-stream] {run_id} done steps={len(steps)}", flush=True)

        # 持久化运行历史（同一会话的多轮）
        history.add_agent_run(
            task=req.task,
            tool_ids=req.tool_ids,
            steps=steps,
            answer=final_text,
            status="ok",
            workspace_id=ws,
            project=ctx.project,
            artifacts=artifacts,
            session_id=session_id,
            turn=len(prior_runs) + 1,
        )
    except Exception as exc:  # noqa: BLE001 — 统一转 SSE error 事件
        print(f"[agent-stream] {run_id} run-error: {type(exc).__name__}: {exc}", flush=True)
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


@router.post("/run")
async def run(req: AgentRunRequest, ws: str = Depends(current_workspace)) -> StreamingResponse:
    """SSE 运行智能体。事件：meta -> (step|artifact)* -> answer_start -> delta* -> done | error。"""
    return StreamingResponse(
        _event_stream(req, ws),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
