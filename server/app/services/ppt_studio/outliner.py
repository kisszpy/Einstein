"""取材流水线：联网检索 + 知识库检索 + LLM 出卡/追问。

事件以 (event, data) 元组形式产出，路由层套「生产者任务 + asyncio.Queue +
15s 心跳」骨架转成 SSE（照 routes/chat.py，长连接不被空闲超时打断）。

模型输出契约是严格 JSON：`{"outline": {...}}` 或 `{"clarification": {...}}`。
歧义规则：`session.clarify_count >= 1` 时提示词里直接要求出卡——追问只有一次。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from app.services import web_augment
from app.services.llm import stream_chat
from app.services.ppt_studio import session_store
from app.services.ppt_studio.outline import Outline

logger = get_logger(__name__)

OUTLINE_SYSTEM = (
    "你是「PPT 大纲策划」：把用户需求变成一份可直接生成的演示文稿确认卡。\n"
    "内容标准：\n"
    "- 标题即结论（Action Title）：每个页标题必须是一句完整的判断——观点 + 数字/结论，"
    "读者只读标题就能听懂整个故事。禁止话题式标题（「XX一览」「XX分析」「XX情况」"
    "「关于XX」都是坏例子）；「2,800 万投入 18 个月回本」「人工成本三年上涨 42%」是好例子；\n"
    "- 封面/章节/结尾页标题除外（它们本来就是话题）；\n"
    "- 每页一个论点，要点 3~6 条；每条 = 观点 + 证据（具体数字、事实或对比），"
    "18~48 字；不允许出现没有数字或事实支撑的「不断提升/持续加强」式空话；\n"
    "- 去 AI 腔：禁用「赋能/抓手/闭环/底层逻辑/全方位/多维度」这类黑话；"
    "各条句式要有变化，不要排比堆砌；写具体的事实与数字，不写口号；\n"
    "- 能用数据说话的页用 stat/table 版式，不要 bullet 流；对比内容用 two_col；\n"
    "- 自查：把全部页标题从上到下连读一遍，应当是一个通顺完整的故事"
    "（水平逻辑），连读不通就改标题；\n"
    "- 注意：这些要点是「骨架」，生成阶段会基于参考资料逐页深化扩写，"
    "所以骨架里务必写出关键数字和论据方向，深化时才有据可依。\n\n"
    "输出契约（严格遵守，只输出一个 JSON 对象，不要任何解释或代码围栏）：\n"
    '需求明确时输出 {"outline": {...}}，结构：\n'
    '{"title": "演示标题", "subtitle": "副标题", "audience": "受众", "lang": "zh",\n'
    ' "sources_policy": "both",\n'
    ' "style": {"theme_pack_id": "", "preset_theme": "", "tone": "", "density": "normal"},\n'
    ' "sections": [{"title": "章节名", "pages": [\n'
    '   {"title": "页标题", "kicker": "眉标", "layout": "bullets",\n'
    '    "bullets": ["要点…"], "note": "讲稿备注",\n'
    '    "sources": [{"id": "w1", "kind": "web", "title": "…"}]}]}],\n'
    ' "sources": [{"id": "w1", "kind": "web", "title": "标题", "url": "……",\n'
    '              "kb_name": "", "snippet": ""}]}\n'
    'layout 只能取：cover / section / bullets / two_col / quote / table / stat / chart / cards / timeline / end / auto。\n'
    "chart：要点是「名称 + 数字」列表（≥3 组）时用，会渲染成图表；"
    "cards：每条要点是「小标题：说明」结构时用，渲染成卡片组；"
    "timeline：里程碑/发展历程/阶段推进类内容用，每条要点以时间开头（如「1948 年：356 亮相」），渲染成时间轴。\n"
    "第一页 layout 用 cover，最后一页用 end；章节转场用 section。\n"
    "来源规则：每个数据/引文必须在页面 sources 里挂对应来源 id，"
    "并把全部来源汇总到顶层 sources（id 与页面引用一致）。\n\n"
    '需求有歧义、缺少关键信息（受众/时长/数据范围）时，只输出 '
    '{"clarification": {"question": "一句话问题", "options": ["可选答案…"]}}，不要猜。\n'
)

NO_CLARIFY_SUFFIX = (
    "\n\n注意：这个会话已经追问过一次，不允许再追问。"
    "信息不足时基于合理假设直接出卡（outline），并在页面 note 里写明假设。"
)

PHASES = {
    "collecting": (10, "正在联网检索"),
    "kb": (35, "正在检索知识库"),
    "outlining": (55, "正在规划大纲"),
    "ready": (100, "确认卡已生成"),
    "clarifying": (100, "需要等待补充信息"),
}


class OutlineParseError(RuntimeError):
    """模型输出不是合法 JSON：error 帧 retryable=true，重试一次可能就好。"""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def phase_frame(phase: str) -> tuple[str, dict]:
    progress, label = PHASES.get(phase, (0, phase))
    return "phase", {"phase": phase, "label": label, "progress": progress}


def _parse_json(text: str) -> dict:
    """剥 ```json 围栏 → json.loads → 失败则 raw_decode 找第一个完整对象。"""
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        try:
            parsed, _end = json.JSONDecoder().raw_decode(cleaned[start:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{", start + 1)
    raise OutlineParseError("模型输出不是合法 JSON")


def _web_sources(ctx: web_augment.WebContext) -> list[dict]:
    return [
        {
            "id": f"w{src.index}",
            "kind": "web",
            "title": src.title or src.url,
            "url": src.url,
            "kb_name": "",
            "snippet": (src.snippet or "")[:600],
        }
        for src in ctx.sources
    ]


def _kb_sources(result: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for i, hit in enumerate(getattr(result, "hits", None) or [], start=1):
        name = hit.document_name
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "id": f"k{i}",
            "kind": "kb",
            "title": name,
            "url": "",
            "kb_name": name,
            "snippet": (hit.text or "")[:600],
        })
    return out


async def analyze(
    session: dict, request: dict, ws: str, uid: str, history: list[tuple[str, str]] | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """出卡流水线：产出 (event, data) 序列。

    终态只有两种：`outline`（含 done）或 `clarification`（含 done）。
    落库（assistant 消息、会话状态）在这里完成，路由层只管转 SSE。
    """
    prompt = str(request.get("prompt") or "").strip()
    web_mode = request.get("web_mode") if request.get("web_mode") in web_augment.MODES else web_augment.MODE_AUTO
    page_target = int(request.get("page_target") or 0)

    yield "meta", {"session_id": session["id"]}

    # ---- 联网取材 ----
    yield phase_frame("collecting")
    web_block = ""
    web_sources: list[dict] = []
    decision = await web_augment.decide(prompt, history or [], web_mode)
    if not decision.search:
        yield "web", {"status": "skipped", "mode": web_mode, "reason": decision.reason}
    else:
        yield "web", {"status": "searching", "mode": web_mode, "query": decision.query, "reason": decision.reason}
        ctx = await web_augment.collect(decision)
        body = web_augment.payload(ctx)
        if ctx.block:
            web_block = ctx.note()
            web_sources = _web_sources(ctx)
            yield "web", {"status": "done", "mode": web_mode, **body}
        else:
            yield "web", {"status": "failed", "mode": web_mode, **body, "message": ctx.error or "没有检索到可用资料"}

    # ---- 知识库取材（retrieve 是 async def）----
    yield phase_frame("kb")
    kb_block = ""
    kb_ids = [str(k) for k in request.get("kb_ids") or session.get("kb_ids") or []]
    kb_sources: list[dict] = []
    if kb_ids:
        from app.services.retrieval import retrieve

        result = await retrieve(kb_ids, prompt, history=history)
        kb_sources = _kb_sources(result)
        if result.error:
            yield "sources", {"sources": web_sources + kb_sources, "warning": result.error}
        else:
            yield "sources", {"sources": web_sources + kb_sources}
        if result.hits:
            from app.services.rag import build_context_block

            kb_block = (
                "=== 知识库参考资料 ===\n"
                + build_context_block(result.hits)
                + "\n=== 知识库参考资料 end ===\n"
            )
    else:
        yield "sources", {"sources": web_sources}

    # ---- LLM 出卡 ----
    yield phase_frame("outlining")
    all_sources = web_sources + kb_sources
    system_text = OUTLINE_SYSTEM
    if session.get("clarify_count", 0) >= 1:
        system_text += NO_CLARIFY_SUFFIX
    if page_target > 0:
        system_text += f"\n\n页数目标：内容页约 {page_target} 页（3~30 页之间）。"
    constraints: list[str] = []
    if request.get("expert_id") or session.get("expert_id"):
        expert_id = request.get("expert_id") or session.get("expert_id")
        try:
            from app.services.experts import get_expert_store

            expert = get_expert_store().visible(str(expert_id), ws, uid)
            if expert and expert.persona:
                constraints.append(f"参考专家人设（风格参考，不改变输出契约）：\n{expert.persona[:600]}")
        except Exception as exc:  # noqa: BLE001 — 专家读取失败不阻断出卡
            logger.warning("读取专家人设失败：%s", exc)
    if session.get("theme_pack_id"):
        constraints.append(
            f"模板约束：使用主题包 {session['theme_pack_id']} 的版式与配色，"
            "不要在 outline 里指定 preset_theme。"
        )
    if web_block:
        constraints.append(web_block)
    if kb_block:
        constraints.append(kb_block)
    if constraints:
        system_text += "\n\n" + "\n\n".join(constraints)
    user_text = prompt
    buf = ""
    raw_text = ""
    async for kind, text in stream_chat(
        [SystemMessage(system_text), HumanMessage(user_text)],
        temperature=0.4,
        workspace_id=ws,
    ):
        if kind == "usage":
            continue
        if kind == "reasoning":
            continue
        raw_text += text
        buf += text
        if len(buf) >= 12:
            yield "delta", {"text": buf}
            buf = ""
    if buf:
        yield "delta", {"text": buf}

    try:
        parsed = _parse_json(raw_text)
    except OutlineParseError as exc:
        yield "error", {"message": f"模型输出解析失败：{exc}", "retryable": True}
        session_store.update_status(session["id"], "failed")
        return

    if "clarification" in parsed:
        data = parsed["clarification"] or {}
        question = str(data.get("question") or data.get("reason") or "").strip()
        if not question:
            question = "这个需求还差一点信息：受众和期望页数分别是什么？"
        options = [str(o) for o in (data.get("options") or [])[:4]]
        session_store.bump_clarify(session["id"])
        session_store.update_status(session["id"], "idle")
        message = session_store.append_message(
            session["id"], "assistant", question, {"clarification": {"question": question, "options": options}}
        )
        yield "clarification", {"question": question, "options": options, "message_id": message["id"]}
        yield "done", {"message_id": message["id"]}
        return

    raw_outline = parsed.get("outline") if isinstance(parsed.get("outline"), dict) else parsed
    try:
        outline = Outline.model_validate(raw_outline).normalize()
    except Exception as exc:  # noqa: BLE001 — 结构不合规按解析失败处理
        yield "error", {"message": f"确认卡结构不合规：{exc}", "retryable": True}
        session_store.update_status(session["id"], "failed")
        return

    # 模型可能漏带来源：把检索到的来源补进卡（页面引用 id 优先）
    known_ids = {s.id for s in outline.sources}
    for src in all_sources:
        if src["id"] not in known_ids:
            outline.sources.append(src)
            known_ids.add(src["id"])
    # 风格由会话配置说了算（模型只负责内容，不擅长挑风格）：
    # 用户在前端选了内置风格/公司模板，出卡时必须回填，否则生成会回落到默认风格。
    session_style = (session.get("theme_pack_id") or "").strip()
    if session_style:
        outline.style.theme_pack_id = session_style
        if session_style.startswith("builtin:"):
            outline.style.preset_theme = session_style.split(":", 1)[1]
    outline = outline.normalize()
    errors = outline.validate()
    if errors:
        yield "error", {"message": f"确认卡校验未通过：{', '.join(errors)}", "retryable": True}
        session_store.update_status(session["id"], "failed")
        return

    session_store.update_status(
        session["id"], "awaiting_confirm", page_count=outline.content_page_count(),
        total_slides=outline.total_slides(),
    )
    message = session_store.append_message(
        session["id"], "assistant", outline.title,
        {"outline": outline.model_dump()},
    )
    yield phase_frame("ready")
    yield "outline", {"outline": outline.model_dump(), "message_id": message["id"]}
    yield "done", {"message_id": message["id"]}
