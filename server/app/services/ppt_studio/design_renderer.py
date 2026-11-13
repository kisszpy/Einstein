"""内置风格的设计渲染器：不依赖任何模板文件，用形状 + 文本框从零绘制版式。

与 renderer.DeckRenderer 的分工：
- DeckRenderer  —— 有公司模板时「反用模板自身」，保母版/字体/配色一致；
- DesignRenderer —— 没模板时按 designs.py 的设计令牌绘制，保证「没模板也好看」。

两套输出都是普通 pptx 形状（矩形/文本框/圆形），用 PowerPoint 打开后每个字、每块色都能改，
不存在「导出图片不可编辑」的问题。

版式清单（与 outline.PAGE_LAYOUTS 对齐）：
cover / section / bullets / two_col / quote / stat / chart / cards / table / end，
另加来源附录页。auto 页由 decide_auto_layout 规则引擎编排（数字成图/卡片组/节奏换气）。

排版原则（避免"丑"的几条硬约束）：
1. 版心 0.95in，正文宽度不超过 10.9in，行长 ≈ 28~34 个汉字；
2. 字号按「条数 × 最长字数」降级，绝不溢出文本框；
3. 深色封面 + 浅色内容页（象牙除外），保证投影与打印都成立；
4. 每页固定三件套：眉标 / 标题 / 细分隔线，页脚只放页码与来源，不放装饰；
5. 中文必须 a:ea/a:cs 同设，否则回落宋体（与 renderer._set_font 同一结论）。
"""

from __future__ import annotations

import io
import math
import re

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.services.ppt_studio.outline import Outline, OutlinePage

EMU_PER_INCH = 914400.0
SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.95
CONTENT_W = SLIDE_W - MARGIN * 2  # 11.433

INK_SIZES = {
    "cover_title": (44, 38, 32),
    "section_title": (34, 30, 26),
    "page_title": (26, 23, 20),
}

# 一份文稿里图表页上限：再多就视觉疲劳，超出回落卡片/要点
MAX_CHART_PAGES = 2
_PLAIN_FLOW = ("bullets", "two_col", "cards")

# 「名称 + 数字（+单位）」结尾的要点 → 可提数成图。
# 单位必须支持复合量词（万台/亿元/万人次…），否则「卡宴 10.3 万台」匹配失败，
# 整页图表会静默回落成纯文字列表。
_CHART_PAIR = re.compile(
    r"^(?P<label>.{1,16}?)\s*[：:]?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|个百分点|(?:万亿|亿|万|千)?[台元人辆次家部个件套吨升度名只场届页字]?)"
    r"\s*[。；；.]?$"
)

# 正文里的数字（含千分位/小数）+ 可选单位 → 强调色加粗，给视线一个落点
_NUM_TOKEN = re.compile(
    r"\d[\d,，.]*\s*(?:%|‰|个百分点|万亿|亿|万|千|倍|个月|年|天|日|小时|分钟|秒"
    r"|人|台|家|次|个|元|美元|米|公里|km|kg|t)?"
)


def extract_chart_series(bullets: list[str]) -> list[tuple[str, float, str]] | None:
    """从「名称 + 数字」要点提取 (label, value, unit) 序列；<3 组或单位混杂则不成图。"""
    pairs: list[tuple[str, float, str]] = []
    for raw in bullets:
        m = _CHART_PAIR.match((raw or "").strip())
        if not m:
            return None
        label = m.group("label").strip().rstrip("，,、的与和")
        pairs.append((label or "未命名", float(m.group("value")), m.group("unit") or ""))
    if len(pairs) < 3 or len(pairs) > 8:
        return None
    if len({u for _, _, u in pairs}) > 1:
        return None
    return pairs


def _stat_like(text: str) -> bool:
    head = (text or "").strip()[:6]
    return any(ch.isdigit() for ch in head) or head.endswith("%")


# 时间/序列型标签 → 柱状图（横条的类别轴放不下长名，柱状轴适合时序）
_TIME_LABEL = re.compile(
    r"\d{4}\s*年?|Q[1-4]|第\s*[一二三四五六七八九十]+\s*季度|"
    r"[01]?\d\s*月|上半年|下半年"
)


def _time_key(label: str) -> float:
    """把「2019 年 / Q3 / 第三季度 / 5 月」解析成可排序的数——折线要求时序升序。"""
    text = (label or "").strip()
    m = re.search(r"Q\s*([1-4])", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"第\s*([一二三四五六七八九十]+)\s*季度", text)
    if m:
        cn = "一二三四五六七八九十".find(m.group(1))
        return float(cn + 1) if cn >= 0 else 0.0
    m = re.search(r"([01]?\d)\s*月", text)
    if m:
        return float(m.group(1))
    m = re.search(r"\d{4}", text)
    if m:
        return float(m.group(0))
    m = re.search(r"\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else 0.0


def pick_chart_kind(pairs: list[tuple[str, float, str]]) -> str:
    """按内容特征挑图表：占比(%)→饼图，时间/序列→柱状，其余→横条排名。

    连续时序（≥4 个全是时间标签的要点）→ 折线：趋势叙事用折线，
    柱状只适合少而离散的时点对比。"""
    units = {u for _, _, u in pairs}
    if units == {"%"} and len(pairs) <= 5:
        return "pie"
    time_pairs = [(_TIME_LABEL.search(label), label) for label, _, _ in pairs]
    if all(hit for hit, _ in time_pairs) and len(pairs) >= 4:
        return "line"
    if any(hit for hit, _ in time_pairs):
        return "column"
    return "bar"


def _card_split(text: str) -> tuple[str, str]:
    """「小标题：说明」拆分；没有合格小标题就整条进正文。"""
    for sep in ("：", ":"):
        if sep in text:
            head, body = text.split(sep, 1)
            head, body = head.strip(), body.strip()
            if 1 <= len(head) <= 12 and body:
                return head, body
    return "", text.strip()


# 页首时间词：「1948 年：…」「2019——…」这类以时间开头的要点 → 时间轴素材
_LEAD_TIME = re.compile(r"^\d{4}\s*年?|^[01]?\d\s*月|^Q[1-4]|^第\s*[一二三四五六七八九十]+\s*季度")


def _timeline_split(text: str) -> tuple[str, str]:
    """「1948 年：356 亮相」→ (时间词, 事件)；拆不出时间就整句进事件。"""
    m = _LEAD_TIME.match((text or "").strip())
    head = m.group(0).strip() if m else ""
    rest = text.strip()[m.end():].lstrip("：:—–-·、 ") if m else text.strip()
    if not head or not rest:
        # 时间词后没有事件文字：把整句当事件，时间取前 10 字
        t = text.strip()
        return t[:10], t[10:].strip() or t
    return head, rest


def decide_auto_layout(
    page: OutlinePage, *, index: int, total: int, history: list[str], chart_used: int
) -> str:
    """auto 页的版式编排规则（确定性，不靠模型挑）。

    优先级：位置固定页 > 数字成图 > 大数字页 > 金句 > 卡片组 > 节奏换气 > 要点流。
    history 是此前已渲染页的版式序列，用于避免连续纯文字流。
    """
    if index == 0:
        return "cover"
    if index == total - 1:
        return "end"
    bullets = [b.strip() for b in page.bullets if b and b.strip()]
    if not bullets:
        return "bullets"
    # 数字成图（限额内）：要点全是「名称+数字」结构
    if extract_chart_series(bullets) and chart_used < MAX_CHART_PAGES:
        return "chart"
    if len(bullets) == 3 and all(_stat_like(b) for b in bullets):
        return "stat"
    # 时间轴：≥3 条以时间开头的要点（里程碑/历史/阶段叙事），排在 stat 之后
    if 3 <= len(bullets) <= 6 and sum(1 for b in bullets if _LEAD_TIME.match(b)) >= max(2, len(bullets) - 1):
        return "timeline"
    if len(bullets) <= 2 and page.title.endswith(("？", "?")):
        return "quote"
    if 3 <= len(bullets) <= 6 and sum(1 for b in bullets if _card_split(b)[0]) >= 3:
        return "cards"
    # 节奏规则：前面连着两页纯文字流，能换版式就换口气
    if len(history) >= 2 and all(h in _PLAIN_FLOW for h in history[-2:]):
        if 4 <= len(bullets) <= 6:
            return "two_col"
    return "bullets"


# ---------------------------------------------------------------- 颜色工具


def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string((value or "000000").lstrip("#").upper())


def _blend(fg: str, bg: str, t: float) -> str:
    """把 fg 以 t 比例混进 bg（t=0 全 bg，t=1 全 fg）——做浅色水印/细线用。"""
    f = (fg or "000000").lstrip("#")
    b = (bg or "FFFFFF").lstrip("#")
    if len(f) != 6 or len(b) != 6:
        return b
    out = []
    for i in (0, 2, 4):
        fv = int(f[i:i + 2], 16)
        bv = int(b[i:i + 2], 16)
        out.append(f"{round(bv + (fv - bv) * max(0.0, min(1.0, t))):02X}")
    return "".join(out)


def _luminance(value: str) -> float:
    v = (value or "").lstrip("#")
    if len(v) != 6:
        return 1.0
    r, g, b = (int(v[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


# ---------------------------------------------------------------- 文本工具


def _set_run(run, size: float, color: str, bold: bool, font: str, spc: int = 0) -> None:
    """字号/颜色/字体/字距；a:ea + a:cs 同设，中文不吃宋体兜底。"""
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = font
    try:
        run.font.color.rgb = _rgb(color)
    except ValueError:
        run.font.color.rgb = _rgb("000000")
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", font)
    if spc:
        rpr.set("spc", str(int(spc)))  # 1/100 pt，做小标签的呼吸感


def _bullets_paragraph(p, color: str, char: str = "▪", indent: float = 0.24) -> None:
    """真项目符号（带悬挂缩进），而不是在文字里敲符号——换行才对得齐。"""
    ppr = p._p.get_or_add_pPr()
    ppr.set("marL", str(int(Inches(indent))))
    ppr.set("indent", str(-int(Inches(indent))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buClr", "a:buFont", "a:buSzPct"):
        for el in ppr.findall(qn(tag)):
            ppr.remove(el)
    bu_clr = ppr.makeelement(qn("a:buClr"), {})
    bu_clr.append(bu_clr.makeelement(qn("a:srgbClr"), {"val": color.lstrip("#").upper()}))
    ppr.append(bu_clr)
    ppr.append(ppr.makeelement(qn("a:buSzPct"), {"val": "90000"}))
    ppr.append(ppr.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
    ppr.append(ppr.makeelement(qn("a:buChar"), {"char": char}))


def _estimate_lines(text: str, width_in: float, size_pt: float) -> int:
    """粗估行数：汉字符宽 ≈ 1em，拉丁 ≈ 0.55em，用于挑字号。"""
    if not text:
        return 0
    per_char = size_pt / 72.0
    width_units = 0.0
    for ch in text:
        width_units += 1.0 if ord(ch) > 0x2E80 else 0.55
    capacity = max(1.0, width_in / per_char)
    return max(1, math.ceil(width_units / capacity))


def _fit_title(text: str, width_in: float, sizes: tuple[int, int, int]) -> int:
    for size in sizes:
        if _estimate_lines(text, width_in, size) <= 2:
            return size
        if size == sizes[0] and _estimate_lines(text, width_in, size) == 2:
            return size
    return sizes[-1]


class DesignRenderer:
    """把确认卡按内置设计风格绘制成 pptx。"""

    def __init__(self, preset: dict, density: str = "normal") -> None:
        self.preset = preset or {}
        self.palette: dict[str, str] = self.preset.get("palette") or {}
        self.font: str = self.preset.get("font") or "Microsoft YaHei"
        self.density = density if density in ("loose", "normal", "dense") else "normal"
        self._total = 0
        self._chapter_of_page: dict[str, int] = {}

    # ------------------------------------------------ 入口

    def render(self, template_bytes: bytes | None, outline: Outline) -> dict:
        """返回与 DeckRenderer 同形的结果（blob/total_slides/layouts_used/degradations/fidelity）。

        template_bytes 参数只为签名兼容；内置风格永远从空白文稿画起。
        """
        prs = Presentation()
        prs.slide_width = Inches(SLIDE_W)
        prs.slide_height = Inches(SLIDE_H)
        blank = self._blank_layout(prs)
        self._drop_existing_slides(prs)

        pages = outline.flatten_pages()
        self._total = len(pages) + (1 if outline.has_appendix() else 0)
        self._index_chapters(outline)

        used: list[str] = []
        degradations: list[str] = []
        for i, page in enumerate(pages):
            if page.layout != "auto":
                layout = page.layout
            else:
                layout = decide_auto_layout(
                    page, index=i, total=len(pages),
                    history=used, chart_used=used.count("chart"),
                )
            used.append(layout)
            slide = prs.slides.add_slide(blank)
            self._strip_placeholders(slide)
            self._paint_background(slide, layout)
            painter = getattr(self, f"_page_{layout}", self._page_bullets)
            fallback = painter(slide, page, outline, i, len(pages))
            # 回落必须留痕：图表/卡片撑不起内容而降级时，明示用户而不是无声变文字
            if fallback:
                degradations.append(f"第 {i + 1} 页「{(page.title or '')[:24]}」{fallback}")
            self._page_footer(slide, outline, page, i, dark=layout in ("cover", "section", "end"),
                              show_title=layout not in ("cover", "end"))
            if page.note:
                slide.notes_slide.notes_text_frame.text = page.note

        if outline.has_appendix():
            slide = prs.slides.add_slide(blank)
            self._strip_placeholders(slide)
            self._paint_background(slide, "appendix")
            self._page_appendix(slide, outline, len(pages), dark=False)
            self._page_footer(slide, outline, None, len(pages), dark=False)

        blob = io.BytesIO()
        prs.save(blob)
        total = len(prs.slides._sldIdLst)  # noqa: SLF001 — 与清空示例页同一处内部结构
        return {
            "blob": blob.getvalue(),
            "total_slides": total,
            "layouts_used": used,
            "degradations": degradations,
            "fidelity": {
                "score": 100,
                "issues": [],
                "degradations": degradations,
                "summary_zh": (
                    f"{total} 页生成完成；内置风格「{self.preset.get('name', '默认')}」"
                    + (f"；{len(degradations)} 处版式降级已如实标注" if degradations else "")
                ),
            },
        }

    # ------------------------------------------------ 基础件

    def _blank_layout(self, prs: Presentation):
        """默认模板连 Blank 版式都带日期/页脚/页码占位符，取占位符最少的那个，
        克隆到页面上的占位符由 _strip_placeholders 统一剥掉。"""
        return min(prs.slide_layouts, key=lambda l: len(list(l.placeholders)))

    def _strip_placeholders(self, slide) -> None:
        for ph in list(slide.placeholders):
            ph._element.getparent().remove(ph._element)  # noqa: SLF001 — 无高层删除 API

    def _drop_existing_slides(self, prs: Presentation) -> None:
        xml_slides = prs.slides._sldIdLst  # noqa: SLF001
        for sld_id in list(xml_slides):
            r_id = sld_id.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            if r_id:
                prs.part.drop_rel(r_id)
            xml_slides.remove(sld_id)

    def _paint_background(self, slide, layout: str) -> None:
        color = self._bg_for(layout)
        self._rect(slide, 0, 0, SLIDE_W, SLIDE_H, fill=color)

    def _bg_for(self, layout: str) -> str:
        p = self.palette
        if layout in ("cover", "section", "end"):
            return p.get("bg", "0B1223")
        return p.get("paper", "FFFFFF")

    def _ink(self, dark: bool) -> str:
        return self.palette.get("on_dark" if dark else "ink", "101828")

    def _muted(self, dark: bool) -> str:
        return self.palette.get("on_dark_muted" if dark else "ink_muted", "5D6B8A")

    def _rect(self, slide, x: float, y: float, w: float, h: float, *,
              fill: str | None = None, line: str | None = None,
              line_w: float = 0.75, rounded: float = 0.0, shape=None):
        kind = shape or (MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE)
        shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
        shp.shadow.inherit = False
        if fill:
            shp.fill.solid()
            shp.fill.fore_color.rgb = _rgb(fill)
        else:
            shp.fill.background()
        if line:
            shp.line.color.rgb = _rgb(line)
            shp.line.width = Pt(line_w)
        else:
            shp.line.fill.background()
        if rounded and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
            try:
                shp.adjustments[0] = rounded
            except (IndexError, ValueError):
                pass
        shp.text_frame.text = ""
        return shp

    def _textbox(self, slide, x: float, y: float, w: float, h: float, *,
                 anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0
        tf.vertical_anchor = anchor
        tf.paragraphs[0].alignment = align
        return box

    def _write(self, slide, x: float, y: float, w: float, h: float, text: str, *,
               size: float, color: str, bold: bool = False, align=PP_ALIGN.LEFT,
               anchor=MSO_ANCHOR.TOP, line_spacing: float = 1.2,
               space_after: float = 0, spc: int = 0, font: str | None = None):
        box = self._textbox(slide, x, y, w, h, anchor=anchor, align=align)
        para = box.text_frame.paragraphs[0]
        para.line_spacing = line_spacing
        if space_after:
            para.space_after = Pt(space_after)
        run = para.add_run()
        run.text = text
        _set_run(run, size, color, bold, font or self.font, spc)
        return box

    # ------------------------------------------------ 页眉/页脚

    def _header(self, slide, page: OutlinePage, outline: Outline, index: int) -> None:
        """内容页页眉：眉标（章节名）+ 标题 + 细分隔线。"""
        p = self.palette
        eyebrow = (page.kicker or "").strip()
        if not eyebrow:
            chapter = self._chapter_of_page.get(page.id or "", 0)
            eyebrow = f"第 {chapter} 章" if chapter else ""
        if eyebrow:
            self._write(slide, MARGIN, 0.72, 8.0, 0.28, eyebrow, size=9.5,
                        color=p.get("accent", "4F6BF6"), bold=True, spc=150)
        title_size = _fit_title(page.title or "未命名页", CONTENT_W, INK_SIZES["page_title"])
        self._write(slide, MARGIN, 1.0, CONTENT_W, 0.9, page.title or "未命名页",
                    size=title_size, color=p.get("ink", "101828"), bold=True,
                    line_spacing=1.12)
        self._rect(slide, MARGIN, 1.98, CONTENT_W, 0.012, fill=p.get("line", "DCE3EF"))
        self._rect(slide, MARGIN, 1.94, 0.62, 0.075, fill=p.get("accent", "4F6BF6"))

    def _page_footer(self, slide, outline: Outline, page: OutlinePage | None,
                     index: int, *, dark: bool, show_title: bool = True) -> None:
        p = self.palette
        muted = self._muted(dark)
        # 来源脚注（page/both 且有引用）：显示来源名而非光秃秃的编号——
        # 「来源：1」对观众毫无信息量
        if page is not None and outline.sources_policy in ("page", "both") and page.sources:
            labels = [self._source_label(outline, ref.id) for ref in page.sources]
            labels = [x for x in labels if x]
            if labels:
                text = "；".join(labels)
                if len(text) > 64:
                    text = text[:60] + "…"
                self._write(slide, MARGIN, 6.63, 8.6, 0.24,
                            f"来源：{text}", size=8.5, color=muted)
        # 左：文稿名（封面/结尾页已有大标题，不重复）；右：页码
        if show_title:
            self._write(slide, MARGIN, 6.94, 7.4, 0.24, (outline.title or "")[:40],
                        size=8, color=muted)
        self._write(slide, SLIDE_W - MARGIN - 1.6, 6.93, 1.6, 0.26,
                    f"{index + 1:02d} / {self._total:02d}", size=9, color=muted,
                    align=PP_ALIGN.RIGHT, spc=40)
        _ = p

    def _source_no(self, outline: Outline, source_id: str) -> str:
        for i, ref in enumerate(outline.sources, start=1):
            if ref.id == source_id:
                return str(i)
        return ""

    def _source_label(self, outline: Outline, source_id: str) -> str:
        for ref in outline.sources:
            if ref.id == source_id:
                label = (ref.title or ref.url or ref.kb_name or "").strip()
                return (label[:30] + "…") if len(label) > 30 else label
        return ""

    def _index_chapters(self, outline: Outline) -> None:
        """页面 id → 所属章节号（页眉眉标用）。章节号按 section 页出现顺序不是唯一来源，
        这里以真实章节序号为准，避免用户插删页后眉标错位。"""
        for i, section in enumerate(outline.sections, start=1):
            for page in section.pages:
                if page.id:
                    self._chapter_of_page[page.id] = i

    def _chapter_title(self, outline: Outline, page: OutlinePage) -> str:
        for section in outline.sections:
            if any(pg.id == page.id for pg in section.pages):
                return section.title
        return ""

    # ------------------------------------------------ 版式：封面

    def _gradient_rect(self, slide, x: float, y: float, w: float, h: float,
                       c1: str, c2: str, angle: int = 45) -> None:
        """双色渐变块；python-pptx 不支持时回落纯色。"""
        shp = self._rect(slide, x, y, w, h)
        try:
            fill = shp.fill
            fill.gradient()
            stops = fill.gradient_stops
            stops[0].color.rgb = _rgb(c1)
            stops[0].position = 0.0
            stops[1].color.rgb = _rgb(c2)
            stops[1].position = 1.0
            if len(stops) > 2:  # 默认模板可能给三停，把中间停并进主色
                for s in list(stops)[2:]:
                    s.color.rgb = _rgb(c1)
            fill.gradient_angle = angle
        except (ValueError, IndexError, AttributeError):
            shp.fill.solid()
            shp.fill.fore_color.rgb = _rgb(c1)
        return shp

    def _grid_lines(self, slide, *, dark: bool) -> None:
        """右侧细网格线组：几乎不可见但给版面「被设计过」的秩序感。"""
        bg = self._bg_for("cover" if dark else "bullets")
        ink = self._ink(dark)
        line = _blend(ink, bg, 0.10)
        # 竖线 3 根（右 2/3 区域）
        for i, x in enumerate((7.6, 9.7, 11.8)):
            self._rect(slide, x, 0.9, 0.008, 5.7, fill=line)
        # 横线 2 根
        for y in (2.4, 4.7):
            self._rect(slide, 7.6, y, 4.9, 0.008, fill=line)

    def _page_cover(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        on_dark = self._ink(True)
        muted = self._muted(True)
        bg = p.get("bg", "0B1223")
        accent = p.get("accent", "4F6BF6")
        accent2 = p.get("accent2", "14B8C4")

        # 品牌标记：小方块 + 字样
        self._rect(slide, MARGIN, 0.72, 0.17, 0.17, fill=accent)
        self._write(slide, MARGIN + 0.30, 0.65, 5.0, 0.3, "AI 演示文稿", size=9,
                    color=muted, spc=120)

        # 右侧装饰三件套：细网格线组 + 渐变强调色块（压右下、出血）+ 实心小方块
        self._grid_lines(slide, dark=True)
        self._gradient_rect(slide, 10.05, 4.85, 4.6, 3.4, accent, accent2, angle=30)
        self._rect(slide, 9.45, 4.15, 0.34, 0.34, fill=accent2)
        # 渐变块上的一行小字：让装饰有信息而非纯色块
        self._write(slide, 10.45, 6.75, 3.8, 0.3, (outline.audience or "").strip()[:22],
                    size=9, color=_blend("FFFFFF", bg, 0.25), spc=100)

        # 三分法：标题落左上交点（kicker 2.05 / 标题 2.4 起）
        kicker = (page.kicker or outline.audience or "").strip()
        if kicker and kicker != (outline.audience or "").strip():
            self._write(slide, MARGIN, 2.05, 8.2, 0.3, kicker, size=10.5,
                        color=accent, bold=True, spc=180)

        title_size = _fit_title(page.title or outline.title or "未命名演示", 9.6,
                                INK_SIZES["cover_title"])
        self._write(slide, MARGIN, 2.4, 9.7, 2.1, page.title or outline.title or "未命名演示",
                    size=title_size, color=on_dark, bold=True, line_spacing=1.12)

        self._rect(slide, MARGIN, 4.62, 1.05, 0.075, fill=accent)

        subtitle = (outline.subtitle or (page.bullets[0] if page.bullets else "")).strip()
        if subtitle:
            self._write(slide, MARGIN, 4.95, 8.6, 1.2, subtitle, size=14.5,
                        color=muted, line_spacing=1.5)

    # ------------------------------------------------ 版式：章节

    def _page_section(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        bg = p.get("bg", "0B1223")
        on_dark = self._ink(True)
        muted = self._muted(True)
        accent = p.get("accent", "4F6BF6")
        chapter = self._chapter_of_page.get(page.id or "", 0) or (index + 1)

        # 右侧大号章节数字：双层错位（浅色底层 + 主色上层）模拟渐变透明
        self._write(slide, 7.55, 1.08, 5.05, 2.5, f"{chapter:02d}", size=132,
                    color=_blend(accent, bg, 0.62), bold=True,
                    align=PP_ALIGN.RIGHT, line_spacing=0.95)
        self._write(slide, 7.35, 0.95, 5.05, 2.5, f"{chapter:02d}", size=132,
                    color=_blend(accent, bg, 0.34), bold=True,
                    align=PP_ALIGN.RIGHT, line_spacing=0.95)

        # 左侧竖向强调条 + 章节序号
        self._rect(slide, MARGIN, 2.42, 0.075, 2.9, fill=accent)
        self._write(slide, MARGIN + 0.28, 2.52, 6.4, 0.3, f"第 {chapter} 章", size=10.5,
                    color=accent, bold=True, spc=160)
        title_size = _fit_title(page.title or "", 7.0, INK_SIZES["section_title"])
        self._write(slide, MARGIN + 0.28, 2.88, 7.0, 1.9, page.title or "", size=title_size,
                    color=on_dark, bold=True, line_spacing=1.16)

        note = (page.kicker or "").strip()
        if note:
            self._write(slide, MARGIN + 0.28, 5.05, 7.4, 1.2, note, size=12, color=muted,
                        line_spacing=1.5)

        # 右下角几何小插画：三级上升圆角条 + 圆点，填充留白又不抢大数字
        for i, h in enumerate((0.42, 0.68, 0.98)):
            self._rect(slide, 10.85 + i * 0.42, 7.06 - h, 0.26, h,
                       fill=_blend(accent, bg, 0.35 + i * 0.22), rounded=0.28)
        self._rect(slide, 12.35, 5.62, 0.2, 0.2, fill=p.get("accent2", "14B8C4"),
                   shape=MSO_SHAPE.OVAL)

    # ------------------------------------------------ 版式：内容页

    def _body_size(self, bullets: list[str]) -> tuple[int, float, float]:
        """正文号/行距/段后（条数 × 最长字数降级），density 再微调一档。

        投影可读性硬线：正文不低于 15pt（业界标准 ≥18pt，但我们的要点
        深化后普遍 18~52 字，16~18pt 与版心容量是平衡点）。"""
        longest = max((len(b) for b in bullets), default=0)
        count = len(bullets)
        if count <= 3 and longest <= 40:
            size, spacing, after = 18, 1.5, 16
        elif count <= 5 and longest <= 56:
            size, spacing, after = 17, 1.45, 14
        elif count <= 6:
            size, spacing, after = 16, 1.42, 12
        elif count <= 7:
            size, spacing, after = 15.5, 1.38, 11
        else:
            size, spacing, after = 15, 1.35, 10
        if longest > 84:
            size -= 1
        if self.density == "loose":
            size += 1
            after += 3
        elif self.density == "dense":
            after = max(6, after - 4)
        return size, spacing, after

    def _page_bullets(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        self._header(slide, page, outline, index)
        bullets = [b.strip() for b in page.bullets if b and b.strip()][:8]
        if not bullets:
            return
        size, spacing, after = self._body_size(bullets)
        # 陈述式版式：≤4 条且不长时，字号上调、整块内容在版心垂直居中——
        # 消灭「要点挤在上 1/3、下面整块空白」的头重脚轻（高级感调研差距 #1）
        statement = len(bullets) <= 4 and max(len(b) for b in bullets) <= 52
        if statement:
            size = min(size + 2, 20)
            spacing = max(spacing, 1.6)
            after += 6
            box = self._textbox(slide, MARGIN, 2.2, 10.72, 4.25, anchor=MSO_ANCHOR.MIDDLE)
        else:
            box = self._textbox(slide, MARGIN, 2.24, 10.72, 4.2)
        tf = box.text_frame
        for i, bullet in enumerate(bullets):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.line_spacing = spacing
            para.space_after = Pt(after)
            _bullets_paragraph(para, p.get("accent", "4F6BF6"))
            self._number_runs(para, bullet[:120], size, p.get("ink", "101828"),
                              p.get("accent", "4F6BF6"))

    def _number_runs(self, para, text: str, size: float, color: str, accent: str) -> None:
        """按数字片段切 run：数字（含单位）强调色加粗且大一档，其余常规——
        关键数据不再淹没在正文里（高级感调研差距 #3）。"""
        pos = 0
        for m in _NUM_TOKEN.finditer(text):
            if m.start() > pos:
                run = para.add_run()
                run.text = text[pos:m.start()]
                _set_run(run, size, color, False, self.font)
            run = para.add_run()
            run.text = m.group(0).strip() + (" " if m.group(0).endswith(" ") else "")
            _set_run(run, size + 1, accent, True, self.font)
            pos = m.end()
        if pos < len(text):
            run = para.add_run()
            run.text = text[pos:]
            _set_run(run, size, color, False, self.font)

    def _page_two_col(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        self._header(slide, page, outline, index)
        bullets = [b.strip() for b in page.bullets if b and b.strip()][:8]
        if not bullets:
            return
        if len(bullets) < 2:
            self._page_bullets(slide, page, outline, index, total)
            return
        mid = math.ceil(len(bullets) / 2)
        columns = [bullets[:mid], bullets[mid:]]
        width = (CONTENT_W - 0.45) / 2
        for col, items in enumerate(columns):
            x = MARGIN + col * (width + 0.45)
            self._rect(slide, x, 2.22, width, 3.95, fill=p.get("surface", "F2F5FB"), rounded=0.03)
            self._rect(slide, x, 2.22, width, 0.075, fill=p.get("accent", "4F6BF6"))
            self._write(slide, x + 0.42, 2.52, 1.2, 0.3, f"0{col + 1}", size=10,
                        color=p.get("accent", "4F6BF6"), bold=True, spc=80)
            box = self._textbox(slide, x + 0.42, 2.92, width - 0.84, 3.0)
            tf = box.text_frame
            for i, bullet in enumerate(items):
                para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                para.line_spacing = 1.42
                para.space_after = Pt(10)
                _bullets_paragraph(para, p.get("accent", "4F6BF6"), indent=0.26)
                self._number_runs(para, bullet[:110], 15, p.get("ink", "101828"),
                                  p.get("accent", "4F6BF6"))

    def _page_chart(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> str | None:
        """原生 pptx 图表页：按内容特征自动选型（饼图=占比 / 折线=趋势 / 柱状=时序 / 横条=排名），
        数据标签直读、值轴隐藏、最大值主角化。

        要点不是「名称+数字」结构时回落要点流，并返回回落原因上报 degradations。"""
        p = self.palette
        self._header(slide, page, outline, index)
        bullets = [b.strip() for b in page.bullets if b and b.strip()]
        pairs = extract_chart_series(bullets)
        if not pairs:
            self._page_bullets(slide, page, outline, index, total)
            return "要点不是「名称+数字」结构，撑不起图表，已回落为要点流"
        kind = pick_chart_kind(pairs)
        unit = pairs[0][2]
        ints = all(v == int(v) for _, v, _ in pairs)
        num_fmt = "0" if ints else "0.0"

        values = [v for _, v, _ in pairs]
        if kind == "line":
            # 折线必须时序升序，标签不是纯时间文本时按 _time_key 排
            pairs = sorted(pairs, key=lambda pr: _time_key(pr[0]))
            values = [v for _, v, _ in pairs]
        data = CategoryChartData()
        if kind == "bar":
            # 横条形图首个类别画在最下，倒序让第一条要点在顶部
            data.categories = [label for label, _, _ in reversed(pairs)]
            data.add_series((page.title or "数值")[:24], tuple(reversed(values)))
        else:
            data.categories = [label for label, _, _ in pairs]
            data.add_series((page.title or "数值")[:24], tuple(values))
        chart_type = {
            "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "pie": XL_CHART_TYPE.PIE,
            "line": XL_CHART_TYPE.LINE_MARKERS,
        }[kind]
        frame = slide.shapes.add_chart(
            chart_type,
            Inches(MARGIN - 0.1), Inches(2.14), Inches(CONTENT_W + 0.2), Inches(4.15), data,
        )
        chart = frame.chart
        chart.has_title = False  # 页眉已有标题，图表自带标题纯重复
        chart.font.size = Pt(11)
        chart.font.name = self.font

        hero = values.index(max(values))
        accent = p.get("accent", "4F6BF6")
        paper = p.get("paper", "FFFFFF")
        plot = chart.plots[0]
        series = plot.series[0]

        if kind == "pie":
            # 饼图：主角强调色，其余按值排名做同色系明度阶梯；图例置底
            rank_of = {idx: r for r, idx in enumerate(
                sorted(range(len(values)), key=lambda i: -values[i]))}
            for idx in range(len(values)):
                point = series.points[idx]
                point.format.fill.solid()
                if idx == hero:
                    point.format.fill.fore_color.rgb = _rgb(accent)
                else:
                    point.format.fill.fore_color.rgb = _rgb(
                        _blend(accent, paper, min(0.34 + rank_of[idx] * 0.15, 0.88)))
            chart.has_legend = True
            legend = chart.legend
            legend.position = XL_LEGEND_POSITION.BOTTOM
            legend.include_in_layout = False
            legend.font.size = Pt(10.5)
            legend.font.color.rgb = _rgb(p.get("ink", "101828"))
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.position = XL_LABEL_POSITION.OUTSIDE_END
            labels.font.size = Pt(10.5)
            labels.font.bold = True
            labels.font.color.rgb = _rgb(p.get("ink", "101828"))
            labels.number_format = f'{num_fmt}"{unit}"'
            labels.number_format_is_linked = False
        elif kind == "line":
            # 折线：强调色连线+圆点标记，峰值点放大；数值直接标在数据点上方
            chart.has_legend = False
            try:
                series.smooth = False  # 趋势图不做平滑曲线，数值感优先
            except (ValueError, AttributeError):
                pass
            series.format.line.color.rgb = _rgb(accent)
            series.format.line.width = Pt(2.5)
            for idx, point in enumerate(series.points):
                marker = point.marker
                marker.style = XL_MARKER_STYLE.CIRCLE
                marker.size = 11 if idx == hero else 7
                marker.format.fill.solid()
                marker.format.fill.fore_color.rgb = _rgb(accent)
                marker.format.line.color.rgb = _rgb(paper)
                marker.format.line.width = Pt(1.25)
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.position = XL_LABEL_POSITION.ABOVE
            labels.font.size = Pt(10.5)
            labels.font.bold = True
            labels.font.color.rgb = _rgb(p.get("ink", "101828"))
            labels.number_format = f'{num_fmt}"{unit}"'
            labels.number_format_is_linked = False

            value_axis = chart.value_axis
            value_axis.has_major_gridlines = False
            value_axis.visible = False
            category_axis = chart.category_axis
            category_axis.has_major_gridlines = False
            category_axis.format.line.color.rgb = _rgb(p.get("line", "DCE3EF"))
            tl = category_axis.tick_labels
            tl.font.size = Pt(11)
            tl.font.color.rgb = _rgb(p.get("ink", "101828"))
        else:
            # 柱/条：整组灰化，只有最大值用强调色——视线先落在主角上
            chart.has_legend = False
            gray = _blend(p.get("ink", "101828"), paper, 0.24)
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = _rgb(gray)
            hero_idx = len(pairs) - 1 - hero if kind == "bar" else hero
            hero_point = series.points[hero_idx]
            hero_point.format.fill.solid()
            hero_point.format.fill.fore_color.rgb = _rgb(accent)
            plot.gap_width = 90
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.position = XL_LABEL_POSITION.OUTSIDE_END
            labels.font.size = Pt(10.5)
            labels.font.bold = True
            labels.font.color.rgb = _rgb(p.get("ink", "101828"))
            labels.number_format = f'{num_fmt}"{unit}"'
            labels.number_format_is_linked = False

            value_axis = chart.value_axis
            value_axis.has_major_gridlines = False
            value_axis.visible = False
            category_axis = chart.category_axis
            category_axis.has_major_gridlines = False
            category_axis.format.line.color.rgb = _rgb(p.get("line", "DCE3EF"))
            tl = category_axis.tick_labels
            tl.font.size = Pt(11)
            tl.font.color.rgb = _rgb(p.get("ink", "101828"))

        if unit:
            self._write(slide, SLIDE_W - MARGIN - 2.0, 1.74, 2.0, 0.26,
                        f"单位：{unit}", size=9, color=p.get("ink_muted", "5D6B8A"),
                        align=PP_ALIGN.RIGHT)
        return None

    def _page_cards(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> str | None:
        """卡片组页：每条要点拆「小标题 + 说明」，2 列圆角卡片。撑不起卡片回落要点流并留痕。"""
        p = self.palette
        self._header(slide, page, outline, index)
        bullets = [b.strip() for b in page.bullets if b and b.strip()][:6]
        cards = [_card_split(b) for b in bullets]
        if len(bullets) < 3:
            self._page_bullets(slide, page, outline, index, total)
            return "可成卡的要点不足 3 条，已回落为要点流"
        rows = math.ceil(len(cards) / 2)
        gap = 0.32
        card_w = (CONTENT_W - gap) / 2
        card_h = min(1.86, (4.18 - gap * (rows - 1)) / rows)
        for idx, (head, body) in enumerate(cards):
            row, col = divmod(idx, 2)
            x = MARGIN + col * (card_w + gap)
            y = 2.2 + row * (card_h + gap)
            self._rect(slide, x, y, card_w, card_h, fill=p.get("surface", "F2F5FB"), rounded=0.045)
            self._rect(slide, x, y, 0.06, card_h, fill=p.get("accent", "4F6BF6"))
            # 圆形数字徽章（白字压强调色圆），比裸文本编号更有图形感
            bx = x + 0.34
            self._rect(slide, bx, y + 0.2, 0.36, 0.36,
                       fill=p.get("accent", "4F6BF6"), shape=MSO_SHAPE.OVAL)
            self._write(slide, bx, y + 0.2, 0.36, 0.36, f"{idx + 1}", size=11,
                        color="FFFFFF", bold=True, align=PP_ALIGN.CENTER,
                        anchor=MSO_ANCHOR.MIDDLE)
            if head:
                self._write(slide, bx + 0.5, y + 0.24, card_w - 1.2, 0.34, head,
                            size=13.5, color=p.get("ink", "101828"), bold=True)
                self._write(slide, x + 0.34, y + 0.78, card_w - 0.68, card_h - 0.96,
                            body[:100], size=11.5, color=p.get("ink_muted", "5D6B8A"),
                            line_spacing=1.3)
            else:
                self._write(slide, bx + 0.5, y + 0.24, card_w - 1.2, card_h - 0.5,
                            body[:110], size=12.5, color=p.get("ink", "101828"),
                            line_spacing=1.35)
        return None

    def _page_timeline(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> str | None:
        """时间轴页：水平轴线 + 节点圆点，标签上下交替——里程碑/历史叙事不再排成要点流。

        要点不足 3 条或时间词不足时回落要点流（由调用方的返回值如实上报）。"""
        p = self.palette
        self._header(slide, page, outline, index)
        bullets = [b.strip() for b in page.bullets if b and b.strip()]
        if len(bullets) < 3 or sum(1 for b in bullets if _LEAD_TIME.match(b)) < 2:
            self._page_bullets(slide, page, outline, index, total)
            return "时间开头的要点不足，撑不起时间轴，已回落为要点流"
        pairs = [_timeline_split(b) for b in bullets[:6]]
        axis_y = 4.05
        axis_h = 0.02
        self._rect(slide, MARGIN, axis_y, CONTENT_W, axis_h, fill=p.get("line", "DCE3EF"))
        n = len(pairs)
        step = CONTENT_W / n
        accent = p.get("accent", "4F6BF6")
        for i, (when, what) in enumerate(pairs):
            cx = MARGIN + step * i + step / 2
            dot = 0.16
            # 节点：外圈浅色 + 内点强调色
            self._rect(slide, cx - dot / 2 - 0.045, axis_y + axis_h / 2 - dot / 2 - 0.045,
                       dot + 0.09, dot + 0.09,
                       fill=_blend(accent, p.get("paper", "FFFFFF"), 0.82), shape=MSO_SHAPE.OVAL)
            self._rect(slide, cx - dot / 2, axis_y + axis_h / 2 - dot / 2, dot, dot,
                       fill=accent, shape=MSO_SHAPE.OVAL)
            above = i % 2 == 0
            box_h = 1.5
            box_w = min(step - 0.24, 2.6)
            if above:
                ty = axis_y - 0.42 - box_h
                line_h = 0.34
                line_y = axis_y - line_h
            else:
                ty = axis_y + 0.42
                line_h = 0.34
                line_y = axis_y + axis_h
            # 节点竖线：把标签挂在轴上
            self._rect(slide, cx - 0.006, line_y, 0.012, line_h,
                       fill=_blend(accent, p.get("paper", "FFFFFF"), 0.55))
            when_size = 17 if len(when) <= 6 else 14
            self._write(slide, cx - box_w / 2, ty, box_w, 0.4, when, size=when_size,
                        color=accent, bold=True, align=PP_ALIGN.CENTER)
            self._write(slide, cx - box_w / 2, ty + 0.42, box_w, box_h - 0.5, what[:64],
                        size=12, color=p.get("ink", "101828"), line_spacing=1.32,
                        align=PP_ALIGN.CENTER)

    def _page_quote(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        self._write(slide, 0.78, 0.30, 3.0, 2.2, "“", size=132,
                    color=p.get("accent_soft", "C9D3FB"), bold=True, line_spacing=0.9)
        quote = (page.bullets[0] if page.bullets else page.title).strip()
        size = 26 if len(quote) <= 46 else 22 if len(quote) <= 70 else 19
        # 垂直居中放在版心，署名紧随其下——避免上重下空
        self._write(slide, 1.55, 1.9, 10.2, 3.4, quote, size=size,
                    color=p.get("ink", "101828"), line_spacing=1.55, bold=False,
                    anchor=MSO_ANCHOR.MIDDLE)
        byline = (page.kicker or "").strip() or (page.title if page.bullets else "")
        if byline:
            self._write(slide, 1.55, 5.45, 10.2, 0.4, f"— {byline}", size=12,
                        color=p.get("ink_muted", "5D6B8A"))

    def _page_stat(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        self._header(slide, page, outline, index)
        stats_all = [b.strip() for b in page.bullets if b and b.strip()]
        stats = stats_all[:3]
        if not stats:
            return
        col_w = CONTENT_W / max(len(stats), 1)
        for i, item in enumerate(stats):
            value, label = self._split_stat(item)
            x = MARGIN + i * col_w
            if i:
                self._rect(slide, x - 0.02, 3.05, 0.008, 2.35, fill=p.get("line", "DCE3EF"))
            # 长值降字号：「15 万台以上」按 44pt 排会折行并压住下方说明
            value_size = 44 if len(value) <= 5 else 36 if len(value) <= 7 else 30 if len(value) <= 10 else 26
            # 按字号估算实际行数，说明文字跟在文字块之后——不再用固定 y 硬碰
            char_w = value_size / 72 * 1.02
            per_line = max(1, int((col_w - 0.55) / char_w))
            lines = max(1, math.ceil(len(value) / per_line))
            value_h = lines * (value_size / 72) * 1.22
            self._write(slide, x + 0.15, 3.1, col_w - 0.5, value_h + 0.12, value, size=value_size,
                        color=p.get("accent", "4F6BF6"), bold=True, line_spacing=1.0)
            label_y = 3.1 + value_h + 0.18
            # 百分比大数字下加进度条：图形化表达「完成度/占比」，不再只是裸数字
            m = re.match(r"^(\d+(?:\.\d+)?)\s*%", value)
            if m:
                pct = max(0.0, min(100.0, float(m.group(1))))
                bar_w = col_w - 0.8
                self._rect(slide, x + 0.15, label_y, bar_w, 0.12,
                           fill=_blend(p.get("accent", "4F6BF6"), p.get("paper", "FFFFFF"), 0.85),
                           rounded=0.5)
                self._rect(slide, x + 0.15, label_y, max(0.12, bar_w * pct / 100), 0.12,
                           fill=p.get("accent", "4F6BF6"), rounded=0.5)
                label_y += 0.3
            self._write(slide, x + 0.15, label_y, col_w - 0.55, 1.6, label, size=13,
                        color=p.get("ink_muted", "5D6B8A"), line_spacing=1.45)
        # 第 4+ 条要点不静默丢弃：取收束句（优先非数字开头）放底部结论条
        extra = stats_all[3:]
        if extra:
            concl = next((e for e in extra if not e[:1].isdigit()), extra[-1])
            concl = re.sub(r"^(?:结论|小结|一句话)[：:]\s*", "", concl)
            self._rect(slide, MARGIN, 6.02, 0.05, 0.42, fill=p.get("accent", "4F6BF6"))
            self._write(slide, MARGIN + 0.2, 6.02, CONTENT_W - 0.4, 0.45, concl[:70],
                        size=12.5, color=p.get("ink", "101828"), bold=True, line_spacing=1.2)

    def _split_stat(self, text: str) -> tuple[str, str]:
        """「数字：说明」拆出大数字与说明。

        head 上限 14 字（「1981–1987 年」9 字也曾被 8 字上限砍成「1981–198」），
        且 head 必须以数字开头——否则「卡宴 10.3 万台」会拆反。
        """
        text = (text or "").strip()
        for sep in ("：", ":", "|"):
            if sep in text:
                head, tail = text.split(sep, 1)
                head, tail = head.strip(), tail.strip()
                if tail and head[:1].isdigit() and 1 <= len(head) <= 14:
                    return head, tail
        return text[:14].strip(), text[14:].strip() or text

    def _page_table(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        """简表：按 | 分列，行间细线。确认卡里没有结构化表格字段时这是最稳的口径。"""
        p = self.palette
        self._header(slide, page, outline, index)
        rows = [b.strip() for b in page.bullets if b and b.strip()][:7]
        if not rows:
            return
        parsed = [[c.strip() for c in r.split("|")] if "|" in r else [r] for r in rows]
        cols = max(len(r) for r in parsed)
        col_w = CONTENT_W / cols
        y = 2.28
        for ri, row in enumerate(parsed):
            if ri:
                self._rect(slide, MARGIN, y - 0.05, CONTENT_W, 0.008,
                           fill=p.get("line", "DCE3EF"))
            for ci, cell in enumerate(row):
                self._write(slide, MARGIN + ci * col_w + 0.12, y, col_w - 0.24, 0.42,
                            cell[:60], size=13 if ri else 13.5,
                            color=p.get("ink", "101828"), bold=(ri == 0))
            y += 0.62

    def _page_end(self, slide, page: OutlinePage, outline: Outline, index: int, total: int) -> None:
        p = self.palette
        on_dark = self._ink(True)
        muted = self._muted(True)
        accent = p.get("accent", "4F6BF6")
        bg = p.get("bg", "0B1223")
        self._rect(slide, MARGIN, 2.05, 0.92, 0.08, fill=accent)
        self._write(slide, MARGIN, 2.23, 8.0, 0.3, "THANK YOU", size=10.5,
                    color=accent, bold=True, spc=240)
        self._write(slide, MARGIN, 2.55, 9.4, 1.4, page.title or "谢谢", size=44,
                    color=on_dark, bold=True, line_spacing=1.1)

        # 收束区：要点里的行动项（或副标题）编号呈现——结尾页不只是「谢谢」
        actions = [b.strip() for b in page.bullets if b and b.strip()][:3]
        if not actions:
            note = (outline.subtitle or "").strip()
            if note:
                self._write(slide, MARGIN, 4.15, 9.2, 1.2, note, size=14, color=muted,
                            line_spacing=1.5)
            return
        self._rect(slide, MARGIN, 4.05, CONTENT_W, 0.01, fill=_blend(on_dark, bg, 0.18))
        for i, action in enumerate(actions):
            y = 4.35 + i * 0.62
            self._write(slide, MARGIN, y, 0.8, 0.4, f"0{i + 1}", size=15,
                        color=accent, bold=True)
            box = self._textbox(slide, MARGIN + 0.85, y, 9.6, 0.55, anchor=MSO_ANCHOR.MIDDLE)
            para = box.text_frame.paragraphs[0]
            self._number_runs(para, action[:80], 13.5, on_dark, accent)

    def _page_appendix(self, slide, outline: Outline, index: int, *, dark: bool) -> None:
        p = self.palette
        sources = outline.appendix_sources()
        self._write(slide, MARGIN, 0.78, 8.0, 0.28, "REFERENCES", size=9.5,
                    color=p.get("accent", "4F6BF6"), bold=True, spc=200)
        self._write(slide, MARGIN, 1.02, CONTENT_W, 0.7, "参考来源", size=24,
                    color=p.get("ink", "101828"), bold=True)
        self._rect(slide, MARGIN, 1.86, CONTENT_W, 0.012, fill=p.get("line", "DCE3EF"))
        self._rect(slide, MARGIN, 1.82, 0.62, 0.075, fill=p.get("accent", "4F6BF6"))

        columns = 2 if len(sources) > 7 else 1
        per_col = math.ceil(len(sources) / columns) if sources else 1
        col_w = (CONTENT_W - 0.5) / columns
        for ci in range(columns):
            chunk = sources[ci * per_col:(ci + 1) * per_col]
            box = self._textbox(slide, MARGIN + ci * (col_w + 0.5), 2.12, col_w, 4.2)
            tf = box.text_frame
            for i, ref in enumerate(chunk):
                no = ci * per_col + i + 1
                label = ref.title or ref.url or ref.kb_name or "未命名来源"
                para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                para.line_spacing = 1.45
                para.space_after = Pt(7)
                lead = para.add_run()
                lead.text = f"{no:02d}  "
                _set_run(lead, 9.5, p.get("accent", "4F6BF6"), True, self.font)
                body = para.add_run()
                body.text = label[:90]
                _set_run(body, 10.5, p.get("ink_muted", "5D6B8A"), False, self.font)
