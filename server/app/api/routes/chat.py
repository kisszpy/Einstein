import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services import web_augment
from app.services.experts import get_expert_store
from app.services.history import get_history_store
from app.services.llm import stream_chat
from app.services.rag import build_context_block, search
from app.services.skill_installer import inline_resources
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
    # 联网：auto 按需判断（默认）/ on 本轮必查 / off 本轮不查
    web_mode: str = web_augment.MODE_AUTO


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
                    block = f"【{skill.name}】\n{skill.system_prompt}"
                    # 安装的技能包可能带参考资料：短文本内联，长资源只提示存在
                    if skill.source == "github" and skill.files:
                        extra = inline_resources(skill.id, skill.files)
                        if extra:
                            block = f"{block}\n\n{extra}"
                    parts.append(block)

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
    yield _sse("meta", {"conversation_id": conversation_id})

    # 所有耗时环节（判定联网、检索、RAG、生成）都在生产者任务里跑，
    # 消费端 15 秒拿不到事件就发一帧注释心跳——否则首字延迟长时
    # 连接会被空闲超时掐断，客户端只收到 meta 就 EOF。
    queue: asyncio.Queue[Any] = asyncio.Queue()
    stop = object()
    answer_parts: list[str] = []

    async def _pump() -> None:
        try:
            store = get_store()
            user_text = req.messages[-1].content
            req_kb_ids = store.owned_kb_ids(ws, req.kb_ids or [])
            system_text, temperature, expert_kb_ids, expert_tool_ids = _compose_system(
                req.expert_id, kb_hit=bool(req_kb_ids), ws=ws
            )
            # 显式传的 kb_ids 优先于专家挂载
            effective_kb_ids = req_kb_ids or expert_kb_ids

            # ---- 按需联网 ----
            mode = req.web_mode if req.web_mode in web_augment.MODES else web_augment.MODE_AUTO
            if "web_search" in expert_tool_ids and mode != web_augment.MODE_OFF:
                # 专家显式挂了联网工具，就默认这轮要查
                mode = web_augment.MODE_ON
            if mode == web_augment.MODE_AUTO:
                await queue.put(("web", {"status": "checking", "mode": mode}))

            history = [(m.role, m.content) for m in req.messages[:-1]]
            decision = await web_augment.decide(user_text, history, mode)
            note = ""
            if not decision.search:
                await queue.put(
                    ("web", {"status": "skipped", "mode": mode, "reason": decision.reason})
                )
            else:
                await queue.put(
                    (
                        "web",
                        {
                            "status": "searching",
                            "mode": mode,
                            "query": decision.query,
                            "reason": decision.reason,
                        },
                    )
                )
                wctx = await web_augment.collect(decision)
                body = web_augment.payload(wctx)
                if wctx.block:
                    note = wctx.note()
                    await queue.put(("web", {"status": "done", "mode": mode, **body}))
                else:
                    await queue.put(
                        (
                            "web",
                            {
                                "status": "failed",
                                "mode": mode,
                                **body,
                                "message": wctx.error or "没有检索到可用资料",
                            },
                        )
                    )

            # ---- 知识库检索 ----
            citations: list[dict] = []
            if effective_kb_ids:
                hits = await search(store, effective_kb_ids, user_text, top_k=4)
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

            if note:
                system_text = f"{system_text}\n\n{note}"
            if citations:
                await queue.put(("citations", {"citations": citations}))

            # ---- 生成 ----
            buf = ""
            async for kind, text in stream_chat(
                _to_lc_messages(req.messages, system_text), temperature=temperature
            ):
                if kind == "usage":
                    continue  # token 用量已在 llm 层落指标，不进对话流
                if kind == "reasoning":
                    await queue.put(("reasoning", {"text": text}))
                else:
                    answer_parts.append(text)
                    # 攒十几字符再发一帧，减少长连接上的帧数
                    buf += text
                    if len(buf) >= 12:
                        await queue.put(("delta", {"text": buf}))
                        buf = ""
            if buf:
                await queue.put(("delta", {"text": buf}))

            # 先落库再发 done：写失败也不影响这一轮回答
            if answer_parts:
                try:
                    history_store = get_history_store()
                    history_store.upsert_conversation(
                        conversation_id=conversation_id,
                        user_text=user_text,
                        assistant_text="".join(answer_parts),
                        title=user_text[:20] or "新会话",
                        workspace_id=ws,
                    )
                    history_store.save()
                except Exception as exc:  # noqa: BLE001 — 持久化失败不回滚回答
                    print(f"[chat] save failed: {type(exc).__name__}: {exc}", flush=True)
            await queue.put(("done", {"conversation_id": conversation_id}))
        except Exception as exc:  # noqa: BLE001 — 异常也走队列，保持事件顺序
            await queue.put(exc)
        finally:
            await queue.put(stop)

    pump = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is stop:
                break
            if isinstance(item, Exception):
                yield _sse("error", {"message": f"{type(item).__name__}: {item}"})
                break
            event, data = item
            yield _sse(event, data)
    finally:
        pump.cancel()
        try:
            await pump
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


@router.post("")
async def chat(req: ChatRequest, ws: str = Depends(current_workspace)) -> StreamingResponse:
    """SSE 流式对话。

    事件：meta -> web(状态) -> [citations] -> (reasoning|delta)* -> done | error
    web 的 status 依次为 checking / searching / done / skipped / failed。
    """
    return StreamingResponse(
        _event_stream(req, ws),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
