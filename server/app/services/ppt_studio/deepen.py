"""深化层：确认卡要点是「骨架」，生成时逐页扩写成成稿文字。

为什么需要这一层：确认卡上的要点就是最终幻灯片文字，内容深度等于出卡那一次
LLM 调用的输出——模型手里只有 600 字摘要，写不出有论据的成稿。深化层在用户
点「确认生成」之后、渲染之前，对每个内容页单独做一次小调用（token 预算充足、
可带该页来源原文），把骨架扩写成 3~6 条 18~52 字、带数字/论据的要点。

纪律：
- 用户在卡上编辑过的要点是权威输入——只补充论据与数据，绝不改变观点和数字；
- 单页失败/解析失败一律回落原文，深化永远不阻断生成；
- 超长先重写一次，仍超长在句边界截断（保数字），绝不静默丢内容；
- 并发信号量限 3，别把本地模型压垮。
"""

from __future__ import annotations

import asyncio
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from app.services.ppt_studio.outline import Outline, OutlinePage

logger = get_logger(__name__)

# 深化只作用于「文字型」内容页；stat/table/quote/cover/section/end 原样渲染
DEEPENABLE_LAYOUTS = ("bullets", "two_col", "cards", "auto")
MIN_BULLETS = 2
MAX_BULLETS = 6
TARGET_MAX_LEN = 52   # 单条目标上限（字）
HARD_MAX_LEN = 64     # 硬上限，超过先重写再截断
CONCURRENCY = 3

DEEPEN_SYSTEM = (
    "你是「演示文稿要点作家」：把一页 PPT 的要点骨架扩写成可直接上台讲的成稿要点。\n"
    "硬性要求：\n"
    "1. 输出 3~6 条要点，每条 18~52 个汉字；\n"
    "2. 每条 = 观点 + 证据（具体数字、事实、对比或因果），数字必须来自骨架或参考资料，"
    "参考资料里没有的数字不许编；\n"
    "3. 禁止空话与 AI 腔：\n"
    "   - 不得出现「不断提升/持续加强/进一步提高/显著增强/稳步推进/不断完善」这类没有数字或事实的结尾；\n"
    "   - 禁用词：赋能、抓手、闭环、护城河、底层逻辑、顶层设计、颗粒度、拉通、沉淀、"
    "全方位、多维度、深层次、新格局、新篇章；\n"
    "   - 每条句式要有变化：不要每条都是「主语+动词+宾语」的相同结构，不要排比堆砌；\n"
    "   - 有对比写对比（X 从 A 到 B）、有归因写归因（因为…所以…），不要平铺陈述；\n"
    "4. 骨架要点是权威输入：每一条的观点和其中的数字都必须保留，只做扩写与合并，"
    "不得改变含义、不得遗漏骨架中的任何一条；\n"
    "5. 像行业分析师写简报，不像 AI 写总结。好坏对比：\n"
    "   坏：「持续推进产线智能化升级，全面提升生产效率」\n"
    "   好：「3,200 台设备联网后，换线时间从 4.2 小时压到 1.5 小时」\n"
    "6. 同时给本页写一份演讲者备注 note：60~130 字、口语化、给台上讲的人看；\n"
    "   备注要讲页面上没写的展开与因果（比如数字背后的背景、两条要点之间的过渡），"
    "   不要复读要点原文，数字同样必须来自骨架或参考资料。\n\n"
    "输出契约（严格只输出一个 JSON 对象）：\n"
    '{"bullets": ["要点一", "要点二", …], "note": "演讲者备注"}\n'
)

_OVERSHOT_NOTE = "\n\n上一次输出有要点超过 52 字，重写时把每条压到 52 字以内，保留全部数字。"


async def _chat_text(system: str, user: str, ws: str) -> str:
    """聚合 stream_chat 为完整文本（deepen 不需要流式）。独立成函数方便测试 monkeypatch。"""
    from app.services.llm import stream_chat

    buf: list[str] = []
    async for kind, text in stream_chat(
        [SystemMessage(system), HumanMessage(user)],
        temperature=0.4,
        workspace_id=ws,
    ):
        if kind in ("usage", "reasoning"):
            continue
        buf.append(text)
    return "".join(buf)


def _parse_bullets(text: str) -> list[str]:
    """剥围栏取 JSON；解析失败返回空列表（调用方回落原文）。"""
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start == -1:
            return []
        try:
            data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, dict):
        return []
    bullets = data.get("bullets")
    if not isinstance(bullets, list):
        return []
    return [str(b).strip() for b in bullets if b and str(b).strip()][:MAX_BULLETS]


def _parse_note(text: str) -> str:
    """从深化输出里取演讲者备注；没有或不是字符串就返回空（调用方保留原 note）。"""
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start == -1:
            return ""
        try:
            data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            return ""
    if not isinstance(data, dict):
        return ""
    note = data.get("note")
    return note.strip() if isinstance(note, str) and note.strip() else ""


def _clamp_sentence(text: str, limit: int = HARD_MAX_LEN) -> str:
    """句边界截断：在 。；！?！? 处找不超过 limit 的最后一句边界，找不到就硬切。"""
    if len(text) <= limit:
        return text
    cut = 0
    for m in re.finditer(r"[。；！？!?]", text[:limit]):
        cut = m.end()
    return text[:cut] if cut >= limit // 2 else text[:limit]


def _page_material(outline: Outline, page: OutlinePage) -> str:
    """该页参考资料：页级 sources 对应的顶层来源摘要（最多 4 条 × 400 字）。"""
    ref_ids = {ref.id for ref in page.sources}
    snippets: list[str] = []
    for src in outline.sources:
        if src.id not in ref_ids:
            continue
        snippet = (src.snippet or "").strip() or (src.title or "").strip()
        if snippet:
            snippets.append(f"- {snippet[:400]}")
        if len(snippets) >= 4:
            break
    return "\n".join(snippets)


def _build_user_text(outline: Outline, page: OutlinePage) -> str:
    parts = [f"页标题：{page.title or ''}"]
    if page.kicker:
        parts.append(f"眉标：{page.kicker}")
    if page.note:
        parts.append(f"讲稿意图：{page.note}")
    parts.append("要点骨架：")
    parts.extend(f"- {b}" for b in page.bullets)
    material = _page_material(outline, page)
    if material:
        parts.append("参考资料（可用其中的数字与事实，没有的不许编）：")
        parts.append(material)
    return "\n".join(parts)


async def _deepen_page(page: OutlinePage, outline: Outline, ws: str,
                       sem: asyncio.Semaphore) -> bool:
    """单页深化。返回是否成功替换（失败回落原文）。"""
    if len(page.bullets) < MIN_BULLETS:
        return False
    user_text = _build_user_text(outline, page)
    async with sem:
        for attempt in (1, 2):
            system_text = DEEPEN_SYSTEM + (_OVERSHOT_NOTE if attempt == 2 else "")
            try:
                raw = await _chat_text(system_text, user_text, ws)
            except Exception as exc:  # noqa: BLE001 — LLM 异常回落原文
                logger.warning("深化页「%s」调用失败：%s", page.title, exc)
                return False
            bullets = _parse_bullets(raw)
            if not bullets:
                continue
            if all(len(b) <= TARGET_MAX_LEN for b in bullets) or attempt == 2:
                page.bullets = [_clamp_sentence(b) for b in bullets]
                # 演讲者备注是深化副产物：模型给了就替换讲稿意图，给不出保留原值
                note = _parse_note(raw)
                if note:
                    page.note = note[:260]
                return True
    return False


async def deepen_outline(outline: Outline, ws: str) -> dict:
    """就地深化 outline 中所有可深化的内容页。返回统计（用于保真度报告）。"""
    sem = asyncio.Semaphore(CONCURRENCY)
    targets = [
        page
        for section in outline.sections
        for page in section.pages
        if page.layout in DEEPENABLE_LAYOUTS
    ]
    results = await asyncio.gather(
        *(_deepen_page(page, outline, ws, sem) for page in targets)
    )
    deepened = sum(1 for ok in results if ok)
    failed = [page.title for page, ok in zip(targets, results) if not ok and len(page.bullets) >= MIN_BULLETS]
    logger.info("要点深化：成功 %d 页，回落 %d 页", deepened, len(failed))
    return {"deepened": deepened, "kept": len(targets) - deepened, "failed": failed}
