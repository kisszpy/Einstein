import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.api.deps import current_uid, current_workspace
from app.core.config import get_settings
from app.services import models as model_registry
from app.services import ratelimit, traces, web_augment
from app.services.experts import get_expert_store
from app.services.history import get_history_store
from app.services import memory as memory_service
from app.services.llm import stream_chat
from app.services.rag import build_context_block
from app.services.retrieval import retrieve
from app.services.skill_installer import inline_resources
from app.services.skills import get_skill_store
from app.services.store import get_store

router = APIRouter(prefix="/chat", tags=["chat"])

SYSTEM_WITH_KB = (
    "你是一个严谨的问答助手。请优先依据下面提供的参考资料回答用户问题，"
    "并在引用到某条资料时在句末标注 [编号]。若资料不足以回答，请明确说明。\n\n"
    "回答要求：第一句直接给结论，再分点展开；用小标题和短列表组织内容；"
    "资料之外的推断要说明是你的判断；不知道就直说，不编造。\n\n"
    "=== 参考资料 ===\n{context}\n=== 参考资料 end ==="
)

SYSTEM_PLAIN = (
    "你是一个乐于助人的中文问答助手。回答要求：第一句话直接回答问题，"
    "再分点展开依据；用小标题和短列表组织内容，一句一个信息点，不写大段流水字；"
    "事实与你的判断分开表述；不确定的内容明确说明，绝不编造数据或引用。"
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[ChatMessage]
    expert_id: str | None = None
    kb_ids: list[str] = []
    # 本轮指定模型（注册表 id）。空 = 专家偏好 → 平台默认 → .env
    model_id: str | None = None
    # 联网：auto 按需判断（默认）/ on 本轮必查 / off 本轮不查
    web_mode: str = web_augment.MODE_AUTO
    # 重新生成：保留旧答案（作为另一个版本），只补一条新的助手消息，
    # 且不让模型看见自己上一版答案（否则它会复读）
    regenerate: bool = False


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
    expert_id: str | None, kb_hit: bool, ws: str, uid: str
) -> tuple[str, float, list[str], list[str], str]:
    """合成系统提示，返回 (system, temperature, kb_ids, tool_ids, model_id)。

    专家 = 人设 + 挂载技能的提示词叠加；未指定专家用默认提示。
    跨工作区的专家与知识库一律忽略，不做任何提示（避免越权探测）。
    `model_id` 是专家自己的模型偏好，空表示跟平台默认走。
    """
    temperature = 0.7
    kb_ids: list[str] = []
    tool_ids: list[str] = []
    model_id = ""
    parts: list[str] = []
    store = get_store()

    if expert_id:
        expert = get_expert_store().visible(expert_id, ws, uid)
        if expert and expert.enabled:
            temperature = expert.temperature
            model_id = getattr(expert, "model_id", "") or ""
            kb_ids = store.owned_kb_ids(ws, list(expert.kb_ids), uid)
            tool_ids = list(expert.tool_ids)
            if expert.persona:
                parts.append(expert.persona)
            skill_store = get_skill_store()
            for sid in expert.skill_ids:
                skill = skill_store.visible(sid, ws, uid)
                if skill and skill.enabled and skill.system_prompt:
                    block = f"【{skill.name}】\n{skill.system_prompt}"
                    # 安装的技能包可能带参考资料：短文本内联，长资源只提示存在
                    if skill.source == "github" and skill.files:
                        extra = inline_resources(skill.id, skill.files)
                        if extra:
                            block = f"{block}\n\n{extra}"
                    parts.append(block)

    if not parts:
        parts = [SYSTEM_PLAIN]

    if kb_hit:
        parts.append(
            "回答时优先依据提供的参考资料，引用到某条资料时在句末标注 [编号]。"
            "若资料不足以回答，请明确说明。"
        )
    return "\n\n".join(parts), temperature, kb_ids, tool_ids, model_id


async def _event_stream(req: ChatRequest, ws: str, uid: str) -> AsyncIterator[str]:
    conversation_id = req.conversation_id or uuid.uuid4().hex
    if req.conversation_id:
        # 续写别人的会话属于越权写入：直接开新会话，不提示也不泄露原会话存在
        existing = get_history_store().get_conversation(req.conversation_id)
        if existing is not None and (existing.workspace_id != ws or existing.owner_id != uid):
            conversation_id = uuid.uuid4().hex
    yield _sse("meta", {"conversation_id": conversation_id})

    if req.regenerate:
        # 末尾可能是上一版答案：截到最后一个 user，模型只看到"问题 + 更早的上下文"
        msgs = list(req.messages)
        while len(msgs) > 1 and msgs[-1].role == "assistant":
            msgs.pop()
        req = req.model_copy(update={"messages": msgs})

    # 所有耗时环节（判定联网、检索、RAG、生成）都在生产者任务里跑，
    # 消费端 15 秒拿不到事件就发一帧注释心跳——否则首字延迟长时
    # 连接会被空闲超时掐断，客户端只收到 meta 就 EOF。
    queue: asyncio.Queue[Any] = asyncio.Queue()
    stop = object()
    answer_parts: list[str] = []

    async def _pump() -> None:
        traces.start(
            kind="chat", workspace_id=ws, owner_id=uid, session_id=conversation_id,
            engine="chat", model=get_settings().llm_model,
        )
        error_kind = ""
        try:
            store = get_store()
            user_text = req.messages[-1].content
            req_kb_ids = store.owned_kb_ids(ws, req.kb_ids or [], uid)
            (
                system_text, temperature, expert_kb_ids, expert_tool_ids, expert_model_id,
            ) = _compose_system(
                req.expert_id, kb_hit=bool(req_kb_ids), ws=ws, uid=uid
            )
            # 模型优先级：本轮显式指定 > 专家偏好 > 平台默认 > .env
            model_id = req.model_id or expert_model_id
            # 轨迹里记真值（start() 时只能填 .env 的模型名）
            traces.note_model(model_registry.resolve(model_id, workspace_id=ws).model)
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

            # ---- 长期记忆注入 ----
            memory_block = ""
            relevant = memory_service.relevant_memory(ws, user_text, top_k=3)
            if relevant:
                memory_block = "\n".join(f"- {e['content']}" for e in relevant)
                system_text = (
                    f"{system_text}\n\n=== 已知背景（来自长期记忆） ===\n{memory_block}\n"
                    "=== 背景结束 ==="
                )

            # ---- 知识库检索 ----
            # 统一走 retrieval 门面：追问指代消解、相关性门限、父片展开、引用
            # 构造都在那里，智能体链路用同一套参数（以前两条链路各配一套，同一
            # 知识库在对话里准、在智能体里碎）。
            citations: list[dict] = []
            if effective_kb_ids:
                result = await retrieve(effective_kb_ids, user_text, history=history)
                citations = result.citations
                if result.error:
                    # 向量与嵌入模型不匹配：如实告知，别让模型拿噪音当资料
                    system_text = (
                        f"{system_text}\n\n[知识库检索失败] {result.error}\n"
                        "请如实告诉用户这次没能检索到知识库内容及其原因，"
                        "不要用参数知识冒充资料。"
                    )
                if result.hits:
                    system_text = (
                        f"{system_text}\n\n=== 参考资料 ===\n{build_context_block(result.hits)}\n"
                        "=== 参考资料 end ==="
                    )

            if note:
                system_text = f"{system_text}\n\n{note}"
            if citations:
                await queue.put(("citations", {"citations": citations}))

            # ---- 生成 ----
            buf = ""
            async for kind, text in stream_chat(
                _to_lc_messages(req.messages, system_text),
                temperature=temperature,
                model_id=model_id,
                workspace_id=ws,
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
                        owner_id=uid,
                        append_user=not req.regenerate,
                    )
                    history_store.save()
                except Exception as exc:  # noqa: BLE001 — 持久化失败不回滚回答
                    print(f"[chat] save failed: {type(exc).__name__}: {exc}", flush=True)
            await queue.put(("done", {"conversation_id": conversation_id}))
        except Exception as exc:  # noqa: BLE001 — 异常也走队列，保持事件顺序
            error_kind = type(exc).__name__
            await queue.put(exc)
        finally:
            try:
                traces.finish(
                    status="ok" if not error_kind else "error", error_kind=error_kind,
                )
            except Exception:  # noqa: BLE001 — 埋点失败不影响回答
                pass
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
async def chat(
    req: ChatRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> StreamingResponse:
    """SSE 流式对话。

    事件：meta -> web(状态) -> [citations] -> (reasoning|delta)* -> done | error
    web 的 status 依次为 checking / searching / done / skipped / failed。
    """
    # Token 日配额：以前只累加不拦截，配了 APP_QUOTA_DAILY_TOKENS 也不生效
    allowed, used = ratelimit.quota_check(ws, "tokens")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"今日 Token 配额已用完（已用 {used}）。配额按自然日重置，或联系管理员调整。",
        )
    return StreamingResponse(
        _event_stream(req, ws, uid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
