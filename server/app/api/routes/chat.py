import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.services.history import get_history_store
from app.services.llm import stream_chat
from app.services.rag import build_context_block, search
from app.services.store import get_store

router = APIRouter(prefix="/chat", tags=["chat"])

SYSTEM_WITH_KB = (
    "你是一个严谨的问答助手。请优先依据下面提供的参考资料回答用户问题，"
    "并在引用到某条资料时在句末标注 [编号]。若资料不足以回答，请明确说明。\n\n"
    "=== 参考资料 ===\n{context}\n=== 参考资料 end ==="
)

SYSTEM_PLAIN = "你是一个乐于助人的中文问答助手，回答简洁准确。"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[ChatMessage]
    expert_id: str | None = None
    kb_ids: list[str] = []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _to_lc_messages(messages: list[ChatMessage], system_text: str) -> list:
    lc: list[AIMessage | HumanMessage] = [SystemMessage(system_text)]  # type: ignore[list-item]
    for m in messages[:-1]:
        if m.role == "assistant":
            lc.append(AIMessage(m.content))
        else:
            lc.append(HumanMessage(m.content))
    lc.append(HumanMessage(messages[-1].content))
    return lc


async def _event_stream(req: ChatRequest) -> AsyncIterator[str]:
    conversation_id = req.conversation_id or uuid.uuid4().hex

    # RAG：挂了知识库就先检索，命中才注入上下文
    citations: list[dict] = []
    system_text = SYSTEM_PLAIN
    if req.kb_ids:
        store = get_store()
        query = req.messages[-1].content
        hits = await search(store, req.kb_ids, query, top_k=4)
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
            system_text = SYSTEM_WITH_KB.format(context=build_context_block(hits))

    yield _sse("meta", {"conversation_id": conversation_id})
    answer_parts: list[str] = []
    try:
        if citations:
            yield _sse("citations", {"citations": citations})

        async for kind, text in stream_chat(_to_lc_messages(req.messages, system_text)):
            if kind == "reasoning":
                yield _sse("reasoning", {"text": text})
            else:
                answer_parts.append(text)
                yield _sse("delta", {"text": text})
        yield _sse("done", {"conversation_id": conversation_id})

        # 持久化会话（错误时不写入）
        if answer_parts:
            user_text = req.messages[-1].content
            history = get_history_store()
            history.upsert_conversation(
                conversation_id=conversation_id,
                user_text=user_text,
                assistant_text="".join(answer_parts),
                title=user_text[:20] or "新会话",
            )
            history.save()
    except Exception as exc:  # noqa: BLE001 — 统一转 SSE error 事件
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


@router.post("")
async def chat(req: ChatRequest) -> StreamingResponse:
    """SSE 流式对话。事件：meta -> [citations] -> delta* -> done | error。"""
    return StreamingResponse(
        _event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
