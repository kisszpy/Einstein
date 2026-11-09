"""对外开放 API（/v1）：给外部系统按 OpenAI 习惯接入。

鉴权：Authorization: Bearer sk-xxx（API Key 绑定工作区，资源隔离按 key 走）。
提供三类能力：
  POST /v1/chat/completions  兼容 OpenAI Chat Completions（流式/非流式），可挂知识库
  POST /v1/search            纯检索（返回命中切片与分数，便于外部做自己的编排）
  POST /v1/agent/run         跑一次智能体（返回步骤轨迹 + 最终答案）
"""

import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.api.deps import require_api_key, require_scope
from app.api.routes.chat import _compose_system
from app.services import audit
from app.services.agent import run_agent
from app.services.apikeys import ApiKey
from app.services.llm import complete, stream_chat
from app.services.rag import build_context_block, search
from app.services.store import get_store

router = APIRouter(prefix="/v1", tags=["openapi"])


class OAIMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[OAIMessage]
    stream: bool = False
    temperature: float | None = None
    # 平台扩展字段（非 OpenAI 标准），用于挂知识库 / 专家
    kb_ids: list[str] = Field(default_factory=list)
    expert_id: str | None = None


class SearchRequest(BaseModel):
    query: str
    kb_ids: list[str] = Field(default_factory=list)
    top_k: int = 4
    mode: str | None = None


class AgentRunRequest(BaseModel):
    task: str
    tool_ids: list[str] = Field(default_factory=list)
    kb_ids: list[str] = Field(default_factory=list)


def _to_lc(messages: list[OAIMessage], system_text: str) -> list:
    lc: list = [SystemMessage(system_text)]
    for m in messages[:-1]:
        lc.append(AIMessage(m.content) if m.role == "assistant" else HumanMessage(m.content))
    lc.append(HumanMessage(messages[-1].content))
    return lc


async def _prepare(req: ChatCompletionRequest, key: ApiKey) -> tuple[list, float, list[dict]]:
    """合成系统提示并做一次检索，返回 (lc 消息, 温度, 引用列表)。"""
    store = get_store()
    kb_ids = store.owned_kb_ids(key.workspace_id, list(req.kb_ids), key.owner_id)
    kb_hit = bool(kb_ids)
    system_text, temperature, expert_kb_ids, _tools = _compose_system(
        req.expert_id, kb_hit=kb_hit, ws=key.workspace_id, uid=key.owner_id
    )
    effective = kb_ids or expert_kb_ids
    citations: list[dict] = []
    if effective:
        hits = await search(store, effective, req.messages[-1].content, top_k=4)
        if hits:
            citations = [
                {
                    "index": i + 1,
                    "document_name": h.document_name,
                    "chunk_id": h.chunk_id,
                    "text": h.text,
                    "score": h.score,
                }
                for i, h in enumerate(hits)
            ]
            system_text = (
                f"{system_text}\n\n=== 参考资料 ===\n{build_context_block(hits)}\n"
                "=== 参考资料 end ==="
            )
    temp = req.temperature if req.temperature is not None else temperature
    return _to_lc(req.messages, system_text), temp, citations


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    request: Request,
    key: ApiKey = Depends(require_api_key),
):
    require_scope(key, "chat")
    if not req.messages:
        return {"error": {"message": "messages 不能为空"}}
    lc_messages, temperature, citations = await _prepare(req, key)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    model_name = req.model or "platform-default"

    if not req.stream:
        text, usage = await complete(lc_messages, temperature=temperature)
        if usage:
            request.state.audit_tokens = (
                usage.get("prompt_tokens") or 0,
                usage.get("completion_tokens") or 0,
            )
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage or {},
            # 平台扩展：引用来源，便于外部系统做溯源展示
            "citations": citations,
        }

    async def _chunks() -> AsyncIterator[str]:
        started = time.perf_counter()
        usage: dict = {}

        def _frame(delta: dict, finish: str | None = None) -> str:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        status = 200
        yield _frame({"role": "assistant", "content": ""})
        try:
            async for kind, text in stream_chat(lc_messages, temperature=temperature):
                if kind == "delta":
                    yield _frame({"content": text})
                elif kind == "usage":
                    try:
                        usage = json.loads(text)
                    except ValueError:
                        usage = {}
            yield _frame({}, finish="stop")
        except Exception as exc:  # noqa: BLE001 — 流内异常转成错误帧
            status = 500
            yield f"data: {json.dumps({'error': {'message': f'{type(exc).__name__}: {exc}'}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        # 流式响应的耗时/token 只有流结束才知道，这里自行落审计
        # （中间件在响应头返回时就会统计，对 SSE 只能记到建连耗时）
        audit.record(
            workspace_id=key.workspace_id,
            key_id=key.id,
            key_name=key.name,
            method="POST",
            path="/v1/chat/completions",
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            tokens_in=usage.get("prompt_tokens") or 0,
            tokens_out=usage.get("completion_tokens") or 0,
            detail="stream",
        )

    # 告知中间件：本次请求的审计由生成器负责，避免重复记账
    request.state.audit_self = True
    return StreamingResponse(
        _chunks(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/search")
async def openapi_search(req: SearchRequest, key: ApiKey = Depends(require_api_key)) -> dict:
    require_scope(key, "search")
    store = get_store()
    kb_ids = store.owned_kb_ids(key.workspace_id, list(req.kb_ids), key.owner_id)
    if not kb_ids:
        return {"hits": [], "note": "没有可检索的知识库（或均不属于该密钥的工作区）"}
    try:
        hits = await search(store, kb_ids, req.query, req.top_k, mode=req.mode)  # type: ignore[arg-type]
    except RuntimeError as exc:  # 嵌入端点不可用：回 503，避免 500 泄漏堆栈
        raise HTTPException(503, str(exc)) from exc
    return {
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "document_name": h.document_name,
                "text": h.text,
                "score": h.score,
                "vector_score": h.vector_score,
                "lexical_score": h.lexical_score,
            }
            for h in hits
        ]
    }


@router.post("/agent/run")
async def openapi_agent_run(
    req: AgentRunRequest, request: Request, key: ApiKey = Depends(require_api_key)
) -> dict:
    require_scope(key, "agent")
    kb_ids = get_store().owned_kb_ids(key.workspace_id, list(req.kb_ids), key.owner_id)
    steps: list[dict] = []
    answer = ""
    async for step in run_agent(req.task, req.tool_ids, kb_ids, key.workspace_id):
        if step["kind"] == "final":
            answer = step["text"]
        else:
            steps.append(step)
    request.state.audit_detail = f"{len(steps)} steps"
    return {"answer": answer, "steps": steps, "workspace_id": key.workspace_id}
