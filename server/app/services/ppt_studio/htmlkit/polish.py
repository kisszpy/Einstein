"""HTML 初稿 → 润色：让模型只改 CSS，改完必须过检测与实测两道关。

为什么不放开结构：下游是「实测 DOM → 原生形状」，靠类名和结构定位。模型一旦
重写 markup，测量的节点就变了一批，pptx 里的形状和文字会对不上——那是最难查的
一类错。CSS 覆盖是唯一能「提升美感」又不打破契约的缝。

三条纪律：
1. 白名单校验。模型返回的 CSS 先过 `sanitize`，只放行可映射属性与设计系统类名；
   危险或越界的一条都不留，并写明为什么丢。
2. 有证据才接受。改完重实测、重体检、重检测，三者任一退步就回退——宁可不好看，
   也不能为了好看把排版弄坏或把内容弄丢。
3. 不为了让检测器闭嘴而改。手册里明说了：检测结果是指控不是判决。改不了就让
   模型写 deferred，我们不替它圆场。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from app.core.logging import get_logger
from app.services.ppt_studio.htmlkit import playbook, skill_bridge
from app.services.ppt_studio.htmlkit.compose import SlideHtml
from app.services.ppt_studio.htmlkit.stylesheet import COMPONENT_CLASSES

logger = get_logger(__name__)

# 补丁上限：给足空间改全篇，又不至于让模型写出一份新样式表
MAX_CSS_CHARS = 4000
MAX_BLOCKS = 40
# 每页只把正文区发给模型；页头样式表在「设计系统」那张卡里已经有了
MAX_MARKUP_CHARS = 1800

_SAMPLED = 2

_ALLOWED = {
    "color", "background-color", "opacity",
    "border-color", "border-width", "border-style",
    "border-top", "border-right", "border-bottom", "border-left",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-radius",
    "font-size", "font-weight", "letter-spacing", "line-height",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "gap", "row-gap", "column-gap",
}

# 这些字眼出现在值里就不是润色，是注入或布局破坏
_FORBIDDEN_VALUE = re.compile(
    r"url\(|expression\(|javascript:|@import|!important|attr\(",
    re.IGNORECASE,
)

_CLASS_TOKEN = r"\.[a-z][a-z0-9-]*"
# 一个节点允许「复合选择器」：`.card.card--topline` 是同一个元素上两个类，
# 不是后代关系——这是 CSS 里最常见的写法之一，不认就只能拒绝掉大半合理补丁。
_PSEUDO = r"(?::nth-child\(\d+\)|:nth-of-type\(\d+\)|:first-child|:last-child)?"
_NODE = rf"(?:{_CLASS_TOKEN})+(?:\[data-kind=\"[a-z_]+\"\])?{_PSEUDO}"
_SELECTOR = re.compile(rf"^{_NODE}(?:[ >]+{_NODE})*$")
# 检测器给的地址是 `div.card:nth-of-type(1)`，带标签名——直接抄进来会被白名单拒。
# 去掉标签前缀留给模型一个能用的选择器，它就不会因为「照抄的地址被丢」而放弃修正。
_TAG_PREFIX = re.compile(r"(?<![\w.-])[a-zA-Z][a-zA-Z0-9]*(?=[.:\[#])")

_NUM = re.compile(r"^(-?(?:\d+(?:\.\d+)?|\.\d+))(px|em|rem|%|vw|vh)?$")
_HEX = re.compile(r"^#(?:[0-9a-f]{3,8})$", re.IGNORECASE)
_RGB = re.compile(r"^rgba?\([^()]*\)$", re.IGNORECASE)
_VAR = re.compile(r"^var\(--[a-z0-9-]+(?:,\s*[^()]+)?\)$", re.IGNORECASE)

_TEXT_WEIGHTS = {"300", "400", "500", "600", "700", "800", "900", "normal", "bold", "bolder"}


@dataclass
class Patch:
    global_css: str = ""
    pages: dict[int, str] = field(default_factory=dict)
    deferred: list[dict] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    # 本轮由哪个技能、哪个版本、哪条命令驱动；进 fidelity，便于回溯
    skill: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.global_css and not self.pages

    def payload(self) -> dict:
        return {
            "global_css": self.global_css,
            "pages": sorted(self.pages),
            "deferred": self.deferred,
            "rejected": self.rejected,
            "skill": self.skill,
        }


# ---------------------------------------------------------------- 校验


def sanitize(css: str) -> tuple[str, list[str]]:
    """只留下合规的规则块。返回 (干净 CSS, 丢弃原因)。"""
    css = re.sub(r"/\*.*?\*/", "", css or "", flags=re.S).strip()
    if not css:
        return "", []
    if len(css) > MAX_CSS_CHARS:
        return "", [f"补丁过长（{len(css)} 字符 > {MAX_CSS_CHARS}），整份丢弃"]
    if "@" in css:
        return "", ["含 @ 规则（@media/@import/@font-face 等），整份丢弃"]

    kept: list[str] = []
    dropped: list[str] = []
    blocks = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    if not blocks:
        return "", ["没有解析出任何规则块"]
    if len(blocks) > MAX_BLOCKS:
        return "", [f"规则块过多（{len(blocks)} > {MAX_BLOCKS}），整份丢弃"]
    for raw_selector, raw_body in blocks:
        selector = " ".join(raw_selector.split())
        bad = _selector_reason(selector)
        if bad:
            dropped.append(f"{selector}：{bad}")
            continue
        decls: list[str] = []
        for decl in raw_body.split(";"):
            decl = decl.strip()
            if not decl:
                continue
            bad = _decl_reason(decl)
            if bad:
                dropped.append(f"{selector} 的 {decl}：{bad}")
                continue
            decls.append(decl)
        if decls:
            kept.append(f"{selector}{{{';'.join(decls)}}}")
    return ";".join(kept), dropped


def _selector_reason(selector: str) -> str:
    if not selector:
        return "空选择器"
    for part in selector.split(","):
        part = part.strip()
        if not _SELECTOR.match(part):
            return "选择器不受支持（只准设计系统类名 + 后代/子代/nth-child）"
        for cls in re.findall(r"\.([a-z][a-z0-9-]*)", part):
            if cls not in COMPONENT_CLASSES:
                return f"未知类名 .{cls}"
    return ""


def _decl_reason(decl: str) -> str:
    if ":" not in decl:
        return "不是合法的声明"
    prop, value = decl.split(":", 1)
    prop = prop.strip().lower()
    value = value.strip()
    if not prop.startswith("--") and prop not in _ALLOWED:
        return "属性不在可映射白名单内"
    if not value or _FORBIDDEN_VALUE.search(value):
        return "值含禁用写法"
    return _value_reason(prop, value)


def _value_reason(prop: str, value: str) -> str:
    lower = value.lower()
    if prop.startswith("--"):
        # 自定义属性本身不排版，只被 var() 消费；限定成颜色或长度，防止拿它塞别的东西
        if _HEX.match(lower) or _RGB.match(lower) or _VAR.match(lower) or lower == "transparent":
            return ""
        if _lengths(lower) is not None or _px(lower) is not None:
            return ""
        return "自定义属性只接受颜色或长度"
    if prop == "font-size":
        size = _px(lower)
        if size is None or not 16 <= size <= 96:
            return "字号须为 16~96px"
        return ""
    if prop == "line-height":
        if lower.endswith("px"):
            return "行高须为无单位倍数"
        num = _px(lower)
        if num is None or not 1.15 <= num <= 2.0:
            return "行高须在 1.15~2.0"
        return ""
    if prop == "letter-spacing":
        num = _px(lower)
        if num is None:
            return "字距须为数值"
        if lower.endswith("em"):
            return "" if -0.04 <= num <= 0.3 else "字距须在 -0.04em~0.3em"
        if lower.endswith("px"):
            return "" if -2 <= num <= 4 else "字距须在 -2~4px"
        return "字距须带单位"
    if prop == "font-weight":
        return "" if lower in _TEXT_WEIGHTS else "字重不在支持范围内"
    if prop == "opacity":
        num = _px(lower)
        return "" if num is not None and 0.6 <= num <= 1 else "不透明度须在 0.6~1"
    if prop == "border-radius":
        nums = _lengths(lower)
        return "" if nums is not None and all(0 <= n <= 24 for n in nums) else "圆角须在 0~24px"
    if prop.endswith("width") and prop.startswith("border"):
        nums = _lengths(lower)
        return "" if nums is not None and all(0 <= n <= 4 for n in nums) else "边框宽须在 0~4px"
    if prop in ("padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
                "gap", "row-gap", "column-gap"):
        nums = _lengths(lower)
        return "" if nums is not None and all(0 <= n <= 64 for n in nums) else "间距须在 0~64px"
    if prop == "border-style":
        return "" if lower in _BORDER_STYLES else "边框样式不受支持"
    if prop in _BORDER_SIDES:
        return _border_shorthand_reason(lower)
    # 颜色类：十六进制、rgb(a)、token 变量、透明、none
    if lower == "none" or _HEX.match(lower) or _RGB.match(lower) or _VAR.match(lower) or lower == "transparent":
        return ""
    return f"不支持的值 {value[:24]}"


_BORDER_SIDES = {"border-top", "border-right", "border-bottom", "border-left"}
_BORDER_STYLES = {"solid", "dashed", "dotted", "none", "double"}


def _border_shorthand_reason(value: str) -> str:
    """`border-left: 0` 和 `border-top: 1px solid #123` 都要认，粗细仍卡 0~4px。"""
    if value in _BORDER_STYLES or value == "0":
        return ""
    parts = value.split()
    if not parts:
        return "边框声明为空"
    nums = _lengths(parts[0])
    if nums is None or len(nums) != 1:
        return "边框须以宽度开头"
    if not 0 <= nums[0] <= 4:
        return "边框宽须在 0~4px"
    if len(parts) >= 2 and parts[1] not in _BORDER_STYLES:
        return "边框样式不受支持"
    if len(parts) >= 3 and not (
        _HEX.match(parts[2]) or _RGB.match(parts[2]) or _VAR.match(parts[2])
        or parts[2] == "transparent"
    ):
        return "边框颜色不受支持"
    return ""


def _px(value: str) -> float | None:
    match = _NUM.match(value)
    return float(match.group(1)) if match else None


def _lengths(value: str) -> list[float] | None:
    nums: list[float] = []
    for token in value.split():
        match = _NUM.match(token)
        if not match or (match.group(2) not in (None, "px")):
            return None
        nums.append(float(match.group(1)))
    return nums or None


# ---------------------------------------------------------------- 解析


def parse(text: str, *, slide_count: int) -> Patch:
    """剥围栏取 JSON。解析失败返回空补丁——润色永远不阻断生成。"""
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    data = None
    try:
        data = json.loads(cleaned)
    except ValueError:
        start = cleaned.find("{")
        if start >= 0:
            try:
                data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return Patch(rejected=["模型输出不是 JSON 对象"])

    patch = Patch()
    global_css, dropped = sanitize(str(data.get("global_css") or ""))
    patch.global_css = global_css
    patch.rejected.extend(f"global_css：{reason}" for reason in dropped)

    pages = data.get("pages")
    if isinstance(pages, list):
        entries: list[tuple[int, str]] = []
        for item in pages:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            css, dropped = sanitize(str(item.get("css") or ""))
            patch.rejected.extend(f"第 {number} 页：{reason}" for reason in dropped)
            if css:
                entries.append((number, css))
        # 契约写的是 1 起页号；出现 0 就说明模型按 0 起编，整批跟着它的口径走，
        # 免得把第 5 页按第 6 页处理——这种偏一页的错比丢掉补丁更难发现。
        zero_based = any(number == 0 for number, _ in entries)
        for number, css in entries:
            index = number if zero_based else number - 1
            if not 0 <= index < slide_count:
                patch.rejected.append(f"pages：页号 {number} 越界（共 {slide_count} 页）")
                continue
            patch.pages[index] = css

    deferred = data.get("deferred")
    if isinstance(deferred, list):
        for item in deferred[:20]:
            if isinstance(item, dict):
                patch.deferred.append({
                    "index": item.get("index"),
                    "rule": str(item.get("rule") or "")[:60],
                    "reason": str(item.get("reason") or "")[:160],
                })
    return patch


# ---------------------------------------------------------------- 提示词


def build_brief(tokens: dict, slides: list[SlideHtml], findings: Iterable) -> str:
    """把设计系统、嫌疑页、抽样页拼成一次请求。样式表不进提示词，进设计卡。"""
    findings = list(findings)
    by_slide: dict[int, list] = {}
    for finding in findings:
        by_slide.setdefault(finding.slide, []).append(finding)

    parts = [
        "【设计系统】",
        playbook.design_card(tokens),
        "",
        f"【整份构成】共 {len(slides)} 页：" +
        "、".join(f"第{s.index + 1}页 {s.kind}" for s in slides),
        "",
        "【有发现的页】",
    ]
    for index in sorted(by_slide):
        if not 0 <= index < len(slides):
            continue
        slide = slides[index]
        parts.append(f"--- 第 {index + 1} 页（{slide.kind}）---")
        parts.append("标记：" + _markup(slide.html))
        for finding in by_slide[index]:
            parts.append(
                f"[{finding.severity}] {finding.rule}（{finding.name}）"
                f" :: {usable_selector(finding.selector) or '—'} :: {finding.detail}"
            )
        parts.append("")

    clean = [s for s in slides if s.index not in by_slide][:_SAMPLED]
    if clean:
        parts.append("【抽样页（没有发现，供你判断全篇一致性；不要为它们输出补丁）】")
        for slide in clean:
            parts.append(f"--- 第 {slide.index + 1} 页（{slide.kind}）---")
            parts.append("标记：" + _markup(slide.html))
            parts.append("")
    orphan = by_slide.pop(-1, [])
    if orphan:
        parts.append("【没能定位到具体页的发现（只改 global_css，别猜页号）】")
        for finding in orphan:
            parts.append(f"[{finding.severity}] {finding.rule}（{finding.name}） :: {usable_selector(finding.selector) or '—'} :: {finding.detail}")
    return "\n".join(parts)


def usable_selector(raw: str) -> str:
    """检测器的元素地址 → 模型能直接写进 CSS 的选择器（去掉标签前缀）。"""
    return _TAG_PREFIX.sub("", raw or "").strip()


def _markup(html: str) -> str:
    """只取正文区。整页 HTML 里有一份完整的样式表，重复十遍既贵又没用。"""
    match = re.search(r'<div class="slide".*?>(.*)</div>\s*</body>', html, re.S)
    body = match.group(1) if match else html
    body = re.sub(r"\s+", " ", body).strip()
    return body[:MAX_MARKUP_CHARS] + ("…" if len(body) > MAX_MARKUP_CHARS else "")


# ---------------------------------------------------------------- 应用


def apply(slides: list[SlideHtml], patch: Patch) -> list[SlideHtml]:
    """把补丁注入到 `</head>` 前。类名与结构保持不变，只多一段覆盖样式。"""
    if patch.empty:
        return slides
    out: list[SlideHtml] = []
    for slide in slides:
        css = ";".join(part for part in (
            patch.global_css,
            patch.pages.get(slide.index, ""),
        ) if part)
        if not css:
            out.append(slide)
            continue
        style = f'<style data-polish="1">{css}</style>'
        html = slide.html.replace("</head>", style + "</head>", 1)
        out.append(SlideHtml(
            index=slide.index, kind=slide.kind, title=slide.title, html=html,
            chapter=slide.chapter, notes=slide.notes, diagnostics=list(slide.diagnostics),
        ))
    return out


# ---------------------------------------------------------------- 调用


def _ask(brief: str, ws: str, system: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.services.llm import stream_chat

    async def run() -> str:
        buf: list[str] = []
        async for kind, text in stream_chat(
            [SystemMessage(system), HumanMessage(brief)],
            temperature=0.3,
            workspace_id=ws,
        ):
            if kind in ("usage", "reasoning"):
                continue
            buf.append(text)
        return "".join(buf)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 渲染器本来就在 asyncio.to_thread 的工作线程里跑，这里没有事件循环
        return asyncio.run(run())
    # 真在事件循环线程里被直接调用时不许嵌套：跳过润色，别把服务卡死
    raise RuntimeError("润色层不能在事件循环线程里同步调用")


def request(tokens: dict, slides: list[SlideHtml], findings: Iterable, ws: str) -> Patch:
    """请求润色补丁。任何异常都降级成空补丁，调用方据此原样出货。"""
    findings = list(findings)
    if not findings:
        return Patch()
    # 设计判断来自已安装的 impeccable 技能；读不到就退回自足判据，不阻断生成
    skill = skill_bridge.resolve(ws)
    system = playbook.compose_system(skill.system if skill else None)
    try:
        text = _ask(build_brief(tokens, slides, findings), ws, system)
    except Exception as exc:  # noqa: BLE001 - 润色是加分项
        logger.warning("润色请求失败，保留初稿：%s", exc)
        return Patch(rejected=[f"模型调用失败：{exc}"])
    patch = parse(text, slide_count=len(slides))
    patch.skill = skill.payload() if skill else {"name": "fallback", "command": "polish"}
    return patch


__all__ = [
    "Patch", "sanitize", "parse", "build_brief", "apply", "request",
    "MAX_CSS_CHARS", "MAX_BLOCKS",
]