"""PPT 生成能力：把结构化的幻灯片数据渲染成 16:9 演示文稿。

设计目标是"可直接拿去讲"：版式系统覆盖叙事骨架（封面/章节/要点/
两栏/引用/表格/结尾）与数据表达（KPI 大数字/条形图/时间线/卡片矩阵/
对比页），让数字和流程被画出来而不是写成 bullet 流。

视觉系统（v2）：
- 深色页（封面/章节/引用/结尾）用对角渐变打底 + 低透明度几何装饰，
  层次感替代纯色平板。
- 卡片一律圆角 + 细描边 + 顶部强调条，替代直角色块。
- 标题区改成「胶囊 kicker + 大号标题」，去掉廉价细线分隔。
- 要点页用强调色小方块引导，主次两级分明。
- 图表条形圆角化 + 渐变填充；表格强调色表头 + 隔行底色。
- 正文按字数自动降级字号，避免长句子破版。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from app.core.logging import get_logger

logger = get_logger(__name__)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
FONT = "Microsoft YaHei"
MARGIN = Inches(0.9)

THEMES: dict[str, dict[str, str]] = {
    "midnight": {
        "name": "午夜蓝",
        "bg": "0B1223",
        "bg2": "1E2A4A",
        "surface": "EEF2FF",
        "surface_line": "DBE3F5",
        "accent": "4F6BF6",
        "accent2": "14B8C4",
        "on_dark": "F8FAFC",
        "on_dark_muted": "A5B4D4",
        "paper": "FFFFFF",
        "ink": "101828",
        "ink_muted": "5D6B8A",
    },
    "graphite": {
        "name": "石墨",
        "bg": "141416",
        "bg2": "2C2C31",
        "surface": "F4F4F5",
        "surface_line": "E4E4E7",
        "accent": "18181B",
        "accent2": "C2A15A",
        "on_dark": "FAFAFA",
        "on_dark_muted": "A1A1AA",
        "paper": "FFFFFF",
        "ink": "18181B",
        "ink_muted": "71717A",
    },
    "ocean": {
        "name": "深海",
        "bg": "041F33",
        "bg2": "0B4A6F",
        "surface": "EAF6FD",
        "surface_line": "CFE8F5",
        "accent": "0E9BD8",
        "accent2": "10B981",
        "on_dark": "F0F9FF",
        "on_dark_muted": "8CC6E8",
        "paper": "FFFFFF",
        "ink": "072B42",
        "ink_muted": "4E7A93",
    },
    "sunset": {
        "name": "暮色",
        "bg": "271A35",
        "bg2": "4A2B58",
        "surface": "FDF0F3",
        "surface_line": "F3DCE4",
        "accent": "E11D63",
        "accent2": "E8A03C",
        "on_dark": "FFF7ED",
        "on_dark_muted": "D8B4C0",
        "paper": "FFFFFF",
        "ink": "2B1B32",
        "ink_muted": "7A5C86",
    },
    "forest": {
        "name": "松林",
        "bg": "0E241C",
        "bg2": "1F4A38",
        "surface": "EDF7F1",
        "surface_line": "D5EADF",
        "accent": "1F9D61",
        "accent2": "D9A514",
        "on_dark": "F0FDF4",
        "on_dark_muted": "86C9A3",
        "paper": "FFFFFF",
        "ink": "0F2A21",
        "ink_muted": "4E7563",
    },
}


def _rgb(hex_str: str) -> RGBColor:
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _set_font(run, size: int, bold: bool = False, color: str = "000000", italic: bool = False) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = FONT
    run.font.color.rgb = _rgb(color)
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", FONT)


def _set_alpha(shape, opacity: float) -> None:
    """给纯色填充叠加透明度（opacity: 0~1，1 为不透明）。"""
    sf = shape.fill._xPr.find(qn("a:solidFill"))
    if sf is None:
        return
    clr = sf.find(qn("a:srgbClr"))
    if clr is None:
        return
    for old in clr.findall(qn("a:alpha")):
        clr.remove(old)
    el = clr.makeelement(qn("a:alpha"), {"val": str(int(opacity * 100000))})
    clr.append(el)


def _rect(slide, x, y, w, h, fill: str, shape=MSO_SHAPE.RECTANGLE,
          line: str | None = None, line_w: float = 0.75, radius: float | None = None):
    """色块。radius 给定时用圆角矩形（0~0.5，相对短边）。"""
    box = slide.shapes.add_shape(shape, x, y, w, h)
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb(fill)
    if line:
        box.line.color.rgb = _rgb(line)
        box.line.width = Pt(line_w)
    else:
        box.line.fill.background()
    box.shadow.inherit = False
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            box.adjustments[0] = radius
        except (IndexError, ValueError) as exc:
            # 该版式的圆角可调项数量不一致，改不动就用模板默认值——
            # 视觉上退一档，但不该让整页生成失败
            logger.debug("圆角调整失败，沿用默认：%s", exc)
    return box


def _grad_rect(slide, x, y, w, h, c1: str, c2: str, angle: float = 115.0) -> None:
    """对角渐变矩形：深色页的底，比纯色更有层次。"""
    box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    box.line.fill.background()
    box.shadow.inherit = False
    fill = box.fill
    fill.gradient()
    try:
        fill.gradient_angle = angle
    except Exception:  # noqa: BLE001 — 某些阅读器不支持角度就保持默认
        pass
    stops = fill.gradient_stops
    stops[0].color.rgb = _rgb(c1)
    stops[0].position = 0.0
    if len(stops) > 1:
        stops[1].color.rgb = _rgb(c2)
        stops[1].position = 1.0
    for extra in list(stops)[2:]:
        extra.color.rgb = _rgb(c2)
    return box


def _decor(slide, theme: dict, variant: str) -> None:
    """深色页的玻璃感几何装饰：大圆低透明度叠放，克制但有层次。"""
    a1 = _rect(slide, SLIDE_W - Inches(4.8), Inches(-2.2), Inches(6.4), Inches(6.4),
               theme["accent"], MSO_SHAPE.OVAL)
    _set_alpha(a1, 0.22)
    a2 = _rect(slide, SLIDE_W - Inches(2.6), Inches(1.4), Inches(3.0), Inches(3.0),
               theme["accent2"], MSO_SHAPE.OVAL)
    _set_alpha(a2, 0.16)
    a3 = _rect(slide, Inches(-1.6), SLIDE_H - Inches(2.6), Inches(3.6), Inches(3.6),
               theme["accent2"], MSO_SHAPE.OVAL)
    _set_alpha(a3, 0.10)
    if variant == "band":
        a4 = _rect(slide, Inches(1.2), SLIDE_H - Inches(0.16), Inches(5.2), Inches(0.16), theme["accent"])
        _set_alpha(a4, 0.85)


def _textbox(slide, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    return tf


def _para(tf, text: str, size: int, color: str, bold=False, first=False, space_before=0,
          space_after=0, line=1.25, align=PP_ALIGN.LEFT, italic=False):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_before = Pt(space_before)
    p.space_after = Pt(space_after)
    p.line_spacing = line
    run = p.add_run()
    run.text = text
    _set_font(run, size, bold=bold, color=color, italic=italic)
    return p


def _para_rich(tf, runs: list[tuple[str, int, str, bool]], first=False, space_before=0,
               line=1.25, align=PP_ALIGN.LEFT):
    """一段里多个样式不同的 run：项目符号染色等场景用。"""
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_before = Pt(space_before)
    p.line_spacing = line
    for text, size, color, bold in runs:
        run = p.add_run()
        run.text = text
        _set_font(run, size, bold=bold, color=color)
    return p


def _footer(slide, text: str, theme: dict, dark: bool, page: int) -> None:
    color = theme["on_dark_muted"] if dark else theme["ink_muted"]
    tf = _textbox(slide, MARGIN, SLIDE_H - Inches(0.58), Inches(7), Inches(0.3))
    if text:
        _para(tf, text, 9.5, color, first=True)
    tf2 = _textbox(slide, SLIDE_W - MARGIN - Inches(1.2), SLIDE_H - Inches(0.58), Inches(1.2), Inches(0.3))
    _para(tf2, f"{page:02d}", 9.5, color, first=True, align=PP_ALIGN.RIGHT)


def _kicker(slide, x: float, y: float, text: str, theme: dict, color: str | None = None) -> None:
    """胶囊小标签：强调色底 + 反白字，比裸字 kicker 精致。"""
    label = (text or "").strip().upper()
    if not label:
        return
    w = Inches(0.24 + 0.115 * min(len(label), 24))
    pill = _rect(slide, x, y, w, Inches(0.32), color or theme["accent"],
                 MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
    _set_alpha(pill, 1.0)
    tfk = _textbox(slide, x, y + Inches(0.02), w, Inches(0.28), MSO_ANCHOR.MIDDLE)
    _para(tfk, label, 10, "FFFFFF", bold=True, first=True, align=PP_ALIGN.CENTER)


def _heading(slide, title: str, theme: dict, kicker: str = "") -> None:
    """浅色内容页的统一标题区：胶囊标签 + 大号标题，留白代替分隔线。"""
    if kicker:
        _kicker(slide, MARGIN, Inches(0.5), kicker, theme)
        title_y = Inches(0.94)
    else:
        title_y = Inches(0.68)
    tf = _textbox(slide, MARGIN, title_y, SLIDE_W - MARGIN * 2, Inches(0.95))
    size = 31 if len(title) <= 22 else 27 if len(title) <= 30 else 23
    _para(tf, title, size, theme["ink"], bold=True, first=True, line=1.15)
    # 标题下强调短条，比全宽细线干净
    _rect(slide, MARGIN, title_y + Inches(0.82), Inches(0.62), Inches(0.07), theme["accent"])


def _body_area(slide, theme: dict):
    return _textbox(slide, MARGIN, Inches(2.05), SLIDE_W - MARGIN * 2, SLIDE_H - Inches(3.0))


def _card(slide, x, y, w, h, theme: dict, dark_paper: bool = False):
    """统一卡片：圆角 + 细描边（浅色页），深色页用表面色块。"""
    if dark_paper:
        return _rect(slide, x, y, w, h, theme["surface"])
    return _rect(slide, x, y, w, h, theme["surface"], MSO_SHAPE.ROUNDED_RECTANGLE,
                 line=theme["surface_line"], radius=0.055)


# ---------- 版式 ----------

COVER_VARIANTS = ("rings", "band", "diag")


def _cover_variant(data: dict) -> str:
    """封面装饰风格：显式指定优先，否则按标题稳定散列——不同 deck 天生不同款。"""
    want = (data.get("variant") or "").strip().lower()
    if want in COVER_VARIANTS:
        return want
    seed = (data.get("title") or "") + (data.get("subtitle") or "")
    return COVER_VARIANTS[sum(seed.encode("utf-8")) % len(COVER_VARIANTS)]


def _slide_title(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _grad_rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"], theme["bg2"], 115.0)
    variant = _cover_variant(data)
    _decor(slide, theme, variant)
    if variant == "band":
        _rect(slide, 0, SLIDE_H - Inches(1.5), SLIDE_W, Inches(1.5), theme["bg2"])
        _set_alpha(slide.shapes[-1], 0.55)
        _rect(slide, 0, SLIDE_H - Inches(1.5), SLIDE_W, Inches(0.06), theme["accent"])
    elif variant == "rings":
        panel = _rect(slide, SLIDE_W - Inches(4.0), 0, Inches(4.0), SLIDE_H, theme["bg2"])
        _set_alpha(panel, 0.45)
        ring = _rect(slide, SLIDE_W - Inches(2.9), Inches(1.7), Inches(2.3), Inches(2.3),
                     theme["accent"], MSO_SHAPE.OVAL, line=theme["on_dark"], line_w=1.2)
        _set_alpha(ring, 0.55)
        _rect(slide, SLIDE_W - Inches(2.35), Inches(2.25), Inches(2.3), Inches(2.3),
              theme["accent2"], MSO_SHAPE.OVAL)

    tf = _textbox(slide, MARGIN, Inches(2.05), Inches(7.5), Inches(3.4))
    title = data.get("title") or meta["title"]
    size = 50 if len(title) <= 14 else 42 if len(title) <= 22 else 32
    _para(tf, title, size, theme["on_dark"], bold=True, first=True, line=1.12)
    subtitle = data.get("subtitle") or meta.get("subtitle") or ""
    if subtitle:
        _para(tf, subtitle, 17, theme["on_dark_muted"], space_before=16, line=1.45)

    _rect(slide, MARGIN, Inches(5.62), Inches(1.2), Inches(0.07), theme["accent"])
    tf2 = _textbox(slide, MARGIN, Inches(5.85), Inches(7), Inches(0.4))
    line = " · ".join(x for x in (meta.get("author"), date.today().strftime("%Y.%m")) if x)
    _para(tf2, line or date.today().strftime("%Y.%m"), 12.5, theme["on_dark_muted"], first=True)


def _slide_section(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _grad_rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"], theme["bg2"], 115.0)
    _decor(slide, theme, "diag")
    num = str(data.get("number") or f"{page:02d}")
    tfa = _rect(slide, MARGIN, Inches(1.9), Inches(3.2), Inches(1.5), theme["accent"], MSO_SHAPE.OVAL)
    _set_alpha(tfa, 0.18)
    tf0 = _textbox(slide, MARGIN, Inches(1.75), Inches(4.5), Inches(1.9))
    _para(tf0, num, 110, theme["accent"], bold=True, first=True)
    tf = _textbox(slide, MARGIN, Inches(3.5), Inches(10), Inches(1.6))
    _para(tf, data.get("title", ""), 36, theme["on_dark"], bold=True, first=True, line=1.2)
    if data.get("subtitle"):
        _para(tf, data["subtitle"], 16, theme["on_dark_muted"], space_before=12)
    _footer(slide, meta["title"], theme, True, page)


def _slide_bullets(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    bullets = _as_list(data.get("bullets"))
    tf = _body_area(slide, theme)
    longest = max((len(b.lstrip("-•· ")) for b in bullets), default=0)
    n = len(bullets)
    size = 20 if n <= 4 and longest <= 34 else 18 if n <= 6 and longest <= 46 else 16 if n <= 8 else 14

    first = True
    for raw in bullets:
        item = raw.strip()
        level = 2 if item.startswith(("-", "•", "·", "  ")) and not item.startswith("--") else 1
        text = item.lstrip("-•· ").strip()
        if not text:
            continue
        if level == 2:
            _para(tf, "      —  " + text, max(size - 3, 12), theme["ink_muted"], first=first,
                  space_before=8, line=1.4)
        else:
            # 强调色小方块引导 + 正文，比"•"字符更干净
            _para_rich(tf, [("▪  ", size, theme["accent"], True), (text, size, theme["ink"], False)],
                       first=first, space_before=12, line=1.42)
        first = False
    _footer(slide, meta["title"], theme, False, page)


def _slide_two_col(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    cols = data.get("columns") or []
    while len(cols) < 2:
        cols.append({"title": "", "bullets": []})
    gap = Inches(0.4)
    col_w = (SLIDE_W - MARGIN * 2 - gap) / 2
    top = Inches(2.05)
    h = SLIDE_H - top - Inches(1.0)
    for i, col in enumerate(cols[:2]):
        x = MARGIN + i * (col_w + gap)
        _card(slide, x, top, col_w, h, theme)
        _rect(slide, x + Inches(0.35), top, Inches(0.5), Inches(0.06),
              theme["accent"] if i == 0 else theme["accent2"])
        tfh = _textbox(slide, x + Inches(0.35), top + Inches(0.38), col_w - Inches(0.7), Inches(0.5))
        head = col.get("title") or ("要点" if i == 0 else "")
        if head:
            _para(tfh, head, 17, theme["ink"], bold=True, first=True)
        tf = _textbox(slide, x + Inches(0.35), top + Inches(1.02), col_w - Inches(0.7), h - Inches(1.35))
        items = _as_list(col.get("bullets"))
        size = 15 if len(items) <= 4 else 13
        first = True
        bullet_color = theme["accent"] if i == 0 else theme["accent2"]
        for it in items[:7]:
            _para_rich(tf, [("▪  ", size - 1, bullet_color, True),
                            (it.lstrip("-•· "), size, theme["ink"], False)],
                       first=first, space_before=8, line=1.4)
            first = False
    _footer(slide, meta["title"], theme, False, page)


def _slide_quote(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _grad_rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"], theme["bg2"], 115.0)
    _decor(slide, theme, "diag")
    # 巨型引号水印
    tfq = _textbox(slide, Inches(1.6), Inches(1.15), Inches(3), Inches(2.2))
    mark = _para(tfq, "\u201c", 130, theme["accent"], bold=True, first=True)
    mark.runs[0].font.color.rgb = _rgb(theme["accent"])
    quote = data.get("quote") or data.get("title", "")
    tf = _textbox(slide, Inches(2.2), Inches(2.9), SLIDE_W - Inches(4.4), Inches(2.4))
    size = 30 if len(quote) <= 40 else 24 if len(quote) <= 70 else 20
    _para(tf, quote, size, theme["on_dark"], first=True, line=1.5)
    if data.get("subtitle"):
        _para(tf, "— " + data["subtitle"], 14, theme["on_dark_muted"], space_before=20)
    _footer(slide, meta["title"], theme, True, page)


def _slide_table(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    headers = [str(h) for h in (data.get("headers") or [])]
    rows = data.get("rows") or []
    if not headers or not rows:
        _slide_bullets(prs, slide, data, theme, meta, page)
        return
    n_rows = len(rows) + 1
    n_cols = len(headers)
    width = SLIDE_W - MARGIN * 2
    height = min(Inches(4.4), Inches(0.5) * n_rows)
    table_shape = slide.shapes.add_table(n_rows, n_cols, MARGIN, Inches(2.05), width, height)
    table = table_shape.table
    for ci, head in enumerate(headers):
        cell = table.cell(0, ci)
        cell.text = ""
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(theme["accent"])
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.margin_left = Inches(0.14)
        _para(tf, head, 13, "FFFFFF", bold=True, first=True)
    for ri, row in enumerate(rows, start=1):
        for ci in range(n_cols):
            cell = table.cell(ri, ci)
            cell.text = ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb("F7F8FB" if ri % 2 else "FFFFFF")
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.margin_left = Inches(0.14)
            val = row[ci] if ci < len(row) else ""
            _para(tf, str(val), 12, theme["ink"], first=True)
    _footer(slide, meta["title"], theme, False, page)


def _slide_end(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _grad_rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"], theme["bg2"], 115.0)
    _decor(slide, theme, "diag")
    tf = _textbox(slide, MARGIN, Inches(2.85), Inches(8), Inches(1.3))
    _para(tf, data.get("title") or "谢谢", 46, theme["on_dark"], bold=True, first=True)
    if data.get("subtitle"):
        _para(tf, data["subtitle"], 16, theme["on_dark_muted"], space_before=16)
    _rect(slide, MARGIN, Inches(4.4), Inches(1.2), Inches(0.07), theme["accent"])


# ---------- 可视化版式（把数字/流程/对比画出来，而不是写成 bullet） ----------

def _stat_items(data: dict) -> list[dict]:
    out = []
    for raw in data.get("stats") or []:
        if isinstance(raw, dict):
            out.append({
                "value": str(raw.get("value", "")).strip()[:14],
                "label": str(raw.get("label", "")).strip()[:18],
                "note": str(raw.get("note", "")).strip()[:40],
            })
        elif isinstance(raw, str) and raw.strip():
            # "38%：权益类配置" 一句话也能渲染
            parts = re.split(r"[：:]", raw.strip(), maxsplit=1)
            out.append({"value": parts[0][:14], "label": parts[1][:18] if len(parts) > 1 else "", "note": ""})
    return [x for x in out if x["value"]][:4]


def _slide_stat(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    items = _stat_items(data)
    n = max(len(items), 1)
    gap = Inches(0.34)
    card_w = (SLIDE_W - MARGIN * 2 - gap * (n - 1)) / n
    top = Inches(2.3) if any(x["note"] for x in items) else Inches(2.6)
    card_h = SLIDE_H - top - Inches(1.3)
    for i, item in enumerate(items):
        x = MARGIN + i * (card_w + gap)
        color = theme["accent"] if i % 2 == 0 else theme["accent2"]
        _card(slide, x, top, card_w, card_h, theme)
        _rect(slide, x + Inches(0.3), top, Inches(0.5), Inches(0.07), color)
        tfv = _textbox(slide, x + Inches(0.3), top + Inches(0.52), card_w - Inches(0.6), Inches(1.2))
        vsize = 46 if len(item["value"]) <= 5 else 36 if len(item["value"]) <= 9 else 28
        _para(tfv, item["value"], vsize, color, bold=True, first=True, line=1.05)
        tfl = _textbox(slide, x + Inches(0.3), top + Inches(1.78), card_w - Inches(0.6), Inches(0.4))
        if item["label"]:
            _para(tfl, item["label"], 15, theme["ink"], bold=True, first=True)
        if item["note"]:
            tfn = _textbox(slide, x + Inches(0.3), top + Inches(2.32), card_w - Inches(0.6), card_h - Inches(2.5))
            _para(tfn, item["note"], 12, theme["ink_muted"], first=True, line=1.4)
    _footer(slide, meta["title"], theme, False, page)


def _num(value: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _slide_chart(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    raw = data.get("items") or []
    bars = []
    for it in raw[:7]:
        if isinstance(it, dict):
            label = str(it.get("label", "")).strip()[:22]
            value = str(it.get("value", "")).strip()
        elif isinstance(it, str) and "：" in it:
            label, value = [x.strip() for x in it.split("：", 1)]
        else:
            continue
        if label:
            bars.append({"label": label, "value": value, "num": _num(value)})
    if not bars:
        _slide_bullets(prs, slide, data, theme, meta, page)
        return
    peak = max((abs(b["num"]) for b in bars if b["num"] is not None), default=0) or 1.0
    label_w = Inches(2.6)
    val_w = Inches(1.15)
    track_w = SLIDE_W - MARGIN * 2 - label_w - val_w
    top = Inches(2.2)
    row_h = min(Inches(0.72), (SLIDE_H - top - Inches(1.25)) / len(bars))
    for i, bar in enumerate(bars):
        y = top + i * (row_h + Inches(0.2))
        tfl = _textbox(slide, MARGIN, y, label_w - Inches(0.2), row_h, MSO_ANCHOR.MIDDLE)
        _para(tfl, bar["label"], 14, theme["ink"], bold=True, first=True)
        frac = min(abs(bar["num"]) / peak, 1.0) if bar["num"] is not None else 0.0
        _rect(slide, MARGIN + label_w, y + row_h * 0.2, track_w, row_h * 0.6,
              theme["surface"], MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
        if frac > 0:
            color = theme["accent"] if i % 2 == 0 else theme["accent2"]
            _rect(slide, MARGIN + label_w, y + row_h * 0.2,
                  max(Emu(int(track_w * frac)), Inches(0.12)), row_h * 0.6,
                  color, MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
        tfv = _textbox(slide, MARGIN + label_w + track_w + Inches(0.15), y, val_w, row_h, MSO_ANCHOR.MIDDLE)
        _para(tfv, bar["value"], 14, theme["ink"], bold=True, first=True)
    if data.get("note"):
        tfn = _textbox(slide, MARGIN, SLIDE_H - Inches(1.05), SLIDE_W - MARGIN * 2, Inches(0.4))
        _para(tfn, data["note"], 11.5, theme["ink_muted"], first=True, italic=True)
    _footer(slide, meta["title"], theme, False, page)


def _slide_timeline(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    steps = []
    for raw in (data.get("steps") or [])[:5]:
        if isinstance(raw, dict):
            steps.append({"label": str(raw.get("label", "")).strip()[:14],
                          "text": str(raw.get("text", "")).strip()[:60]})
        elif isinstance(raw, str) and raw.strip():
            parts = re.split(r"[：:]", raw.strip(), maxsplit=1)
            steps.append({"label": parts[0][:14], "text": parts[1][:60] if len(parts) > 1 else ""})
    if not steps:
        _slide_bullets(prs, slide, data, theme, meta, page)
        return
    n = len(steps)
    gap = Inches(0.34)
    card_w = (SLIDE_W - MARGIN * 2 - gap * (n - 1)) / n
    top = Inches(2.8)
    line_y = top - Inches(0.44)
    _rect(slide, MARGIN + Inches(0.3), line_y, SLIDE_W - MARGIN * 2 - Inches(0.6), Emu(19050),
          theme["surface_line"])
    for i, step in enumerate(steps):
        x = MARGIN + i * (card_w + gap)
        dot = Inches(0.26)
        color = theme["accent"] if i % 2 == 0 else theme["accent2"]
        _rect(slide, x + card_w / 2 - dot / 2, line_y - dot / 2, dot, dot, color, MSO_SHAPE.OVAL)
        halo = _rect(slide, x + card_w / 2 - dot * 0.95, line_y - dot * 0.95,
                     dot * 1.9, dot * 1.9, color, MSO_SHAPE.OVAL)
        _set_alpha(halo, 0.18)
        _card(slide, x, top, card_w, SLIDE_H - top - Inches(1.3), theme)
        _rect(slide, x + Inches(0.26), top, Inches(0.44), Inches(0.06), color)
        tfl = _textbox(slide, x + Inches(0.26), top + Inches(0.32), card_w - Inches(0.52), Inches(0.45))
        if step["label"]:
            _para(tfl, step["label"], 16, theme["ink"], bold=True, first=True)
        tft = _textbox(slide, x + Inches(0.26), top + Inches(0.95), card_w - Inches(0.52),
                       SLIDE_H - top - Inches(2.4))
        if step["text"]:
            _para(tft, step["text"], 13, theme["ink_muted"], first=True, line=1.45)
    _footer(slide, meta["title"], theme, False, page)


def _slide_grid(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    cards = []
    for raw in (data.get("cards") or [])[:6]:
        if isinstance(raw, dict):
            cards.append({"title": str(raw.get("title", "")).strip()[:20],
                          "text": str(raw.get("text", "")).strip()[:90]})
        elif isinstance(raw, str) and raw.strip():
            parts = re.split(r"[：:]", raw.strip(), maxsplit=1)
            cards.append({"title": parts[0][:20], "text": parts[1][:90] if len(parts) > 1 else ""})
    if not cards:
        _slide_bullets(prs, slide, data, theme, meta, page)
        return
    n = len(cards)
    cols = 2 if n <= 4 else 3
    rows_n = (n + cols - 1) // cols
    gap = Inches(0.32)
    card_w = (SLIDE_W - MARGIN * 2 - gap * (cols - 1)) / cols
    top = Inches(2.1)
    card_h = min(Inches(2.3), (SLIDE_H - top - Inches(1.15) - gap * (rows_n - 1)) / rows_n)
    for i, card in enumerate(cards):
        r, c = divmod(i, cols)
        x = MARGIN + c * (card_w + gap)
        y = top + r * (card_h + gap)
        color = theme["accent"] if (r + c) % 2 == 0 else theme["accent2"]
        _card(slide, x, y, card_w, card_h, theme)
        _rect(slide, x + Inches(0.32), y, Inches(0.46), Inches(0.06), color)
        tfh = _textbox(slide, x + Inches(0.32), y + Inches(0.5), card_w - Inches(0.64), Inches(0.5))
        if card["title"]:
            _para(tfh, card["title"], 16, theme["ink"], bold=True, first=True)
        tft = _textbox(slide, x + Inches(0.32), y + Inches(1.14), card_w - Inches(0.64), card_h - Inches(1.35))
        if card["text"]:
            _para(tft, card["text"], 12.5, theme["ink_muted"], first=True, line=1.45)
    _footer(slide, meta["title"], theme, False, page)


def _slide_vs(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    gap = Inches(0.7)
    col_w = (SLIDE_W - MARGIN * 2 - gap) / 2
    top = Inches(2.05)
    h = SLIDE_H - top - Inches(1.0)
    sides = []
    for key, color in (("left", theme["accent"]), ("right", theme["accent2"])):
        raw = data.get(key) or {}
        if isinstance(raw, str):
            raw = {"bullets": _as_list(raw)}
        sides.append({
            "title": str(raw.get("title", "")).strip()[:24],
            "items": _as_list(raw.get("bullets"))[:6],
            "color": color,
        })
    for i, side in enumerate(sides):
        x = MARGIN + i * (col_w + gap)
        _card(slide, x, top, col_w, h, theme)
        _rect(slide, x + Inches(0.35), top, Inches(0.5), Inches(0.06), side["color"])
        tfh = _textbox(slide, x + Inches(0.35), top + Inches(0.36), col_w - Inches(0.7), Inches(0.5))
        if side["title"]:
            _para(tfh, side["title"], 17, theme["ink"], bold=True, first=True)
        tf = _textbox(slide, x + Inches(0.35), top + Inches(1.02), col_w - Inches(0.7), h - Inches(1.35))
        size = 15 if len(side["items"]) <= 4 else 13
        first = True
        for it in side["items"]:
            _para_rich(tf, [("▪  ", size - 1, side["color"], True),
                            (it.lstrip("-•· "), size, theme["ink"], False)],
                       first=first, space_before=8, line=1.4)
            first = False
    # 中缝 VS 徽章
    cx = MARGIN + col_w + gap / 2
    badge = Inches(0.7)
    _rect(slide, cx - badge / 2, top + h / 2 - badge / 2, badge, badge, theme["bg"], MSO_SHAPE.OVAL)
    tfb = _textbox(slide, cx - badge / 2, top + h / 2 - badge / 2, badge, badge, MSO_ANCHOR.MIDDLE)
    _para(tfb, "VS", 15, theme["on_dark"], bold=True, first=True, align=PP_ALIGN.CENTER)
    _footer(slide, meta["title"], theme, False, page)


_RENDERERS = {
    "title": _slide_title,
    "section": _slide_section,
    "bullets": _slide_bullets,
    "two_col": _slide_two_col,
    "quote": _slide_quote,
    "table": _slide_table,
    "stat": _slide_stat,
    "chart": _slide_chart,
    "timeline": _slide_timeline,
    "grid": _slide_grid,
    "vs": _slide_vs,
    "end": _slide_end,
}


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [ln.strip() for ln in re.split(r"\n|；|;", value) if ln.strip()]
    if isinstance(value, dict):
        return [f"{k}：{v}" for k, v in value.items()]
    return [str(x) for x in value]


def _infer_layout(data: dict, index: int, total: int) -> str:
    layout = (data.get("layout") or "").strip().lower()
    if layout in _RENDERERS:
        return layout
    if index == 0:
        return "title"
    if index == total - 1 and not data.get("bullets") and not data.get("columns"):
        return "end"
    if data.get("rows") and data.get("headers"):
        return "table"
    if data.get("stats"):
        return "stat"
    if data.get("steps"):
        return "timeline"
    if data.get("cards"):
        return "grid"
    if data.get("left") or data.get("right"):
        return "vs"
    if data.get("items"):
        return "chart"
    if data.get("columns"):
        return "two_col"
    if data.get("quote"):
        return "quote"
    if not data.get("bullets") and not data.get("title"):
        return "section"
    return "bullets"


def _safe_filename(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", (name or "").strip()).strip("-")
    return (s or "presentation")[:60]


def build_deck(payload: dict) -> dict:
    """payload: {title, subtitle, author, theme, slides:[...], filename}"""
    theme_key = (payload.get("theme") or "midnight").strip().lower()
    if theme_key not in THEMES:
        theme_key = "midnight"
    theme = THEMES[theme_key]

    raw_slides = payload.get("slides") or []
    if isinstance(raw_slides, str):
        raw_slides = [{"title": payload.get("title", ""), "bullets": raw_slides}]
    slides = [s for s in raw_slides if isinstance(s, dict)]
    if not slides:
        slides = [
            {"layout": "title", "title": payload.get("title", "演示文稿"), "subtitle": payload.get("subtitle", "")},
            {"layout": "bullets", "title": "要点", "bullets": ["（未提供内容）"]},
        ]

    meta = {
        "title": payload.get("title") or slides[0].get("title") or "演示文稿",
        "subtitle": payload.get("subtitle") or "",
        "author": payload.get("author") or "",
    }

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank = prs.slide_layouts[6]

    used: list[str] = []
    total = len(slides)
    for idx, data in enumerate(slides):
        layout = _infer_layout(data, idx, total)
        slide = prs.slides.add_slide(blank)
        _RENDERERS[layout](prs, slide, data, theme, meta, idx + 1)
        used.append(layout)

    filename = _safe_filename(payload.get("filename") or meta["title"]) + ".pptx"
    return {"presentation": prs, "filename": filename, "layouts": used, "theme": theme_key, "slides": total}


def write_pptx(payload: dict, out_path: Path) -> dict:
    built = build_deck(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    built["presentation"].save(str(out_path))
    return {
        "file": created_name(out_path),
        "path": str(out_path),
        "slides": built["slides"],
        "layouts": built["layouts"],
        "theme": built["theme"],
        "theme_name": THEMES[built["theme"]]["name"],
    }


def created_name(path: Path) -> str:
    return path.name
