import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.experts import get_expert_store
from app.services.history import get_history_store
from app.services.llm import stream_chat
from app.services.rag import build_context_block, search
from app.services.skills import get_skill_store
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


def _compose_system(
    expert_id: str | None, kb_hit: bool, ws: str
) -> tuple[str, float, list[str], list[str]]:
    """合成系统提示，返回 (system, temperature, kb_ids, tool_ids)。

    专家 = 人设 + 挂载技能的提示词叠加；未指定专家用默认提示。
    跨工作区的专家与知识库一律忽略，不做任何提示（避免越权探测）。
    """
    temperature = 0.7
    kb_ids: list[str] = []
    tool_ids: list[str] = []
    parts: list[str] = []
    store = get_store()

    if expert_id:
        expert = get_expert_store().get(expert_id)
        if expert and expert.enabled and expert.workspace_id == ws:
            temperature = expert.temperature
            kb_ids = store.owned_kb_ids(ws, list(expert.kb_ids))
            tool_ids = list(expert.tool_ids)
            if expert.persona:
                parts.append(expert.persona)
            skill_store = get_skill_store()
            for sid in expert.skill_ids:
                skill = skill_store.get(sid)
                if skill and skill.enabled and skill.system_prompt:
                    parts.append(f"【{skill.name}】\n{skill.system_prompt}")

    if not parts:
        parts = ["你是一个乐于助人的中文问答助手，回答简洁准确。"]

    if kb_hit:
        parts.append(
            "回答时优先依据提供的参考资料，引用到某条资料时在句末标注 [编号]。"
            "若资料不足以回答，请明确说明。"
        )
    return "\n\n".join(parts), temperature, kb_ids, tool_ids


async def _event_stream(req: ChatRequest, ws: str) -> AsyncIterator[str]:
    conversation_id = req.conversation_id or uuid.uuid4().hex

    expert_id = req.expert_id
    store = get_store()
    req_kb_ids = store.owned_kb_ids(ws, req.kb_ids or [])
    system_text, temperature, expert_kb_ids, _tool_ids = _compose_system(
        expert_id, kb_hit=bool(req_kb_ids), ws=ws
    )
    # 显式传的 kb_ids 优先于专家挂载
    effective_kb_ids = req_kb_ids or expert_kb_ids

    # RAG：挂了知识库就先检索，命中才注入上下文
    citations: list[dict] = []
    if effective_kb_ids:
        query = req.messages[-1].content
        hits = await search(store, effective_kb_ids, query, top_k=4)
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

    yield _sse("meta", {"conversation_id": conversation_id})
    answer_parts: list[str] = []
    try:
        if citations:
            yield _sse("citations", {"citations": citations})

        async for kind, text in stream_chat(
            _to_lc_messages(req.messages, system_text), temperature=temperature
        ):
            if kind == "usage":
                continue  # token 用量已在 llm 层落指标，不进对话流
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
                workspace_id=ws,
            )
            history.save()
    except Exception as exc:  # noqa: BLE001 — 统一转 SSE error 事件
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


@router.post("")
async def chat(req: ChatRequest, ws: str = Depends(current_workspace)) -> StreamingResponse:
    """SSE 流式对话。事件：meta -> [citations] -> delta* -> done | error。"""
    return StreamingResponse(
        _event_stream(req, ws),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
