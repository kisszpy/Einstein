"""PPT 生成能力：把结构化的幻灯片数据渲染成 16:9 演示文稿。

设计目标是"可直接拿去讲"，所以做了完整的版式系统（封面/章节/要点/
两栏/引用/表格/结尾）、五套配色和中英文混排的字重层级，而不是堆一个
默认模板。所有尺寸按 13.333×7.5 英寸（16:9）硬编码，正文按字数自动
降级字号，避免长句子破版。
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

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
FONT = "Microsoft YaHei"
MARGIN = Inches(0.9)

THEMES: dict[str, dict[str, str]] = {
    "midnight": {
        "name": "午夜蓝",
        "bg": "0F172A",
        "surface": "1E293B",
        "accent": "6366F1",
        "accent2": "22D3EE",
        "on_dark": "F8FAFC",
        "on_dark_muted": "94A3B8",
        "paper": "FFFFFF",
        "ink": "0F172A",
        "ink_muted": "64748B",
    },
    "graphite": {
        "name": "石墨",
        "bg": "18181B",
        "surface": "27272A",
        "accent": "F4F4F5",
        "accent2": "A1A1AA",
        "on_dark": "FAFAFA",
        "on_dark_muted": "A1A1AA",
        "paper": "FFFFFF",
        "ink": "18181B",
        "ink_muted": "71717A",
    },
    "ocean": {
        "name": "深海",
        "bg": "06283D",
        "surface": "0E3A52",
        "accent": "38BDF8",
        "accent2": "34D399",
        "on_dark": "F0F9FF",
        "on_dark_muted": "7DD3FC",
        "paper": "FFFFFF",
        "ink": "06283D",
        "ink_muted": "5B7A8C",
    },
    "sunset": {
        "name": "暮色",
        "bg": "2B1B32",
        "surface": "3D2748",
        "accent": "FB7185",
        "accent2": "FBBF24",
        "on_dark": "FFF7ED",
        "on_dark_muted": "D8B4C0",
        "paper": "FFFFFF",
        "ink": "2B1B32",
        "ink_muted": "7A5C86",
    },
    "forest": {
        "name": "松林",
        "bg": "132A22",
        "surface": "1D3B30",
        "accent": "4ADE80",
        "accent2": "FDE68A",
        "on_dark": "F0FDF4",
        "on_dark_muted": "86C9A3",
        "paper": "FFFFFF",
        "ink": "132A22",
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


def _rect(slide, x, y, w, h, fill: str, shape=MSO_SHAPE.RECTANGLE):
    box = slide.shapes.add_shape(shape, x, y, w, h)
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb(fill)
    box.line.fill.background()
    box.shadow.inherit = False
    return box


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


def _footer(slide, text: str, theme: dict, dark: bool, page: int) -> None:
    color = theme["on_dark_muted"] if dark else theme["ink_muted"]
    tf = _textbox(slide, MARGIN, SLIDE_H - Inches(0.62), Inches(7), Inches(0.3))
    if text:
        _para(tf, text, 10, color, first=True)
    tf2 = _textbox(slide, SLIDE_W - MARGIN - Inches(1), SLIDE_H - Inches(0.62), Inches(1), Inches(0.3))
    _para(tf2, f"{page:02d}", 10, color, first=True, align=PP_ALIGN.RIGHT)


def _heading(slide, title: str, theme: dict, kicker: str = "") -> None:
    """浅色内容页的统一标题区：强调色短条 + （可选小标签）+ 标题 + 细分隔线。"""
    if kicker:
        _rect(slide, MARGIN, Inches(0.52), Inches(0.42), Inches(0.065), theme["accent"])
        tfk = _textbox(slide, MARGIN + Inches(0.56), Inches(0.42), Inches(8), Inches(0.28))
        _para(tfk, kicker.upper(), 11, theme["accent"], bold=True, first=True)
        title_y = Inches(0.86)
    else:
        _rect(slide, MARGIN, Inches(0.66), Inches(0.42), Inches(0.075), theme["accent"])
        title_y = Inches(0.88)
    tf = _textbox(slide, MARGIN, title_y, SLIDE_W - MARGIN * 2, Inches(0.9))
    size = 30 if len(title) <= 22 else 26 if len(title) <= 30 else 22
    _para(tf, title, size, theme["ink"], bold=True, first=True, line=1.15)
    _rect(slide, MARGIN, Inches(1.72), SLIDE_W - MARGIN * 2, Emu(9525), theme["ink_muted"])


def _body_area(slide, theme: dict):
    return _textbox(slide, MARGIN, Inches(1.95), SLIDE_W - MARGIN * 2, SLIDE_H - Inches(2.9))


# ---------- 版式 ----------

def _slide_title(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"])
    # 右侧几何装饰：两层错位色块
    _rect(slide, SLIDE_W - Inches(4.2), 0, Inches(4.2), SLIDE_H, theme["surface"])
    _rect(slide, SLIDE_W - Inches(4.2), 0, Inches(0.12), SLIDE_H, theme["accent"])
    _rect(slide, SLIDE_W - Inches(3.1), Inches(1.6), Inches(2.4), Inches(2.4), theme["accent"], MSO_SHAPE.OVAL)
    _rect(slide, SLIDE_W - Inches(2.6), Inches(2.1), Inches(2.4), Inches(2.4), theme["accent2"], MSO_SHAPE.OVAL)

    tf = _textbox(slide, MARGIN, Inches(2.15), Inches(7.4), Inches(3.2))
    title = data.get("title") or meta["title"]
    size = 46 if len(title) <= 16 else 38 if len(title) <= 24 else 30
    _para(tf, title, size, theme["on_dark"], bold=True, first=True, line=1.1)
    subtitle = data.get("subtitle") or meta.get("subtitle") or ""
    if subtitle:
        _para(tf, subtitle, 18, theme["on_dark_muted"], space_before=18, line=1.4)

    _rect(slide, MARGIN, Inches(5.6), Inches(1.1), Inches(0.06), theme["accent"])
    tf2 = _textbox(slide, MARGIN, Inches(5.85), Inches(7), Inches(0.4))
    line = " · ".join(x for x in (meta.get("author"), date.today().strftime("%Y.%m")) if x)
    _para(tf2, line or date.today().strftime("%Y.%m"), 12, theme["on_dark_muted"], first=True)


def _slide_section(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"])
    _rect(slide, 0, 0, Inches(0.16), SLIDE_H, theme["accent"])
    num = data.get("number") or f"{page:02d}"
    tf0 = _textbox(slide, MARGIN, Inches(2.0), Inches(3), Inches(1.6))
    _para(tf0, str(num), 96, theme["surface"], bold=True, first=True)
    tf = _textbox(slide, MARGIN, Inches(3.35), Inches(9.5), Inches(1.5))
    _para(tf, data.get("title", ""), 34, theme["on_dark"], bold=True, first=True, line=1.2)
    if data.get("subtitle"):
        _para(tf, data["subtitle"], 16, theme["on_dark_muted"], space_before=12)
    _footer(slide, meta["title"], theme, True, page)


def _slide_bullets(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    bullets = _as_list(data.get("bullets"))
    tf = _body_area(slide, theme)
    # 按条数与篇幅自动降字号，保证不溢出
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
        gap = 14 if level == 1 else 6
        if level == 2:
            _para(tf, "— " + text, max(size - 3, 12), theme["ink_muted"], first=first,
                  space_before=gap, line=1.4)
        else:
            _para(tf, "•  " + text, size, theme["ink"], first=first, space_before=gap, line=1.45)
        first = False
    _footer(slide, meta["title"], theme, False, page)


def _slide_two_col(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["paper"])
    _heading(slide, data.get("title", ""), theme, data.get("kicker", ""))
    cols = data.get("columns") or []
    while len(cols) < 2:
        cols.append({"title": "", "bullets": []})
    gap = Inches(0.35)
    col_w = (SLIDE_W - MARGIN * 2 - gap) / 2
    top = Inches(2.0)
    h = SLIDE_H - top - Inches(1.0)
    for i, col in enumerate(cols[:2]):
        x = MARGIN + i * (col_w + gap)
        _rect(slide, x, top, col_w, h, "F4F4F5" if theme["paper"] == "FFFFFF" else theme["surface"])
        _rect(slide, x, top, col_w, Inches(0.06), theme["accent"] if i == 0 else theme["accent2"])
        tfh = _textbox(slide, x + Inches(0.35), top + Inches(0.4), col_w - Inches(0.7), Inches(0.5))
        head = col.get("title") or ("要点" if i == 0 else "")
        if head:
            _para(tfh, head, 17, theme["ink"], bold=True, first=True)
        tf = _textbox(slide, x + Inches(0.35), top + Inches(1.05), col_w - Inches(0.7), h - Inches(1.4))
        items = _as_list(col.get("bullets"))
        size = 15 if len(items) <= 4 else 13
        first = True
        for it in items[:7]:
            _para(tf, "· " + it.lstrip("-•· "), size, theme["ink"], first=first, space_before=8, line=1.4)
            first = False
    _footer(slide, meta["title"], theme, False, page)


def _slide_quote(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"])
    _rect(slide, Inches(2.2), Inches(2.55), Inches(0.9), Inches(0.07), theme["accent"])
    quote = data.get("quote") or data.get("title", "")
    tf = _textbox(slide, Inches(2.2), Inches(2.85), SLIDE_W - Inches(4.4), Inches(2.4))
    size = 30 if len(quote) <= 40 else 24 if len(quote) <= 70 else 20
    _para(tf, f"“{quote}”", size, theme["on_dark"], first=True, line=1.5, italic=True)
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
    table_shape = slide.shapes.add_table(n_rows, n_cols, MARGIN, Inches(2.0), width, height)
    table = table_shape.table
    for ci, head in enumerate(headers):
        cell = table.cell(0, ci)
        cell.text = ""
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(theme["bg"])
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.margin_left = Inches(0.14)
        _para(tf, head, 13, theme["on_dark"], bold=True, first=True)
    for ri, row in enumerate(rows, start=1):
        for ci in range(n_cols):
            cell = table.cell(ri, ci)
            cell.text = ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb("FAFAFA" if ri % 2 else "FFFFFF")
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.margin_left = Inches(0.14)
            val = row[ci] if ci < len(row) else ""
            _para(tf, str(val), 12, theme["ink"], first=True)
    _footer(slide, meta["title"], theme, False, page)


def _slide_end(prs, slide, data: dict, theme: dict, meta: dict, page: int) -> None:
    _rect(slide, 0, 0, SLIDE_W, SLIDE_H, theme["bg"])
    _rect(slide, SLIDE_W - Inches(3.4), SLIDE_H - Inches(3.4), Inches(2.6), Inches(2.6), theme["accent"], MSO_SHAPE.OVAL)
    tf = _textbox(slide, MARGIN, Inches(2.9), Inches(8), Inches(1.2))
    _para(tf, data.get("title") or "谢谢", 44, theme["on_dark"], bold=True, first=True)
    if data.get("subtitle"):
        _para(tf, data["subtitle"], 16, theme["on_dark_muted"], space_before=16)
    _rect(slide, MARGIN, Inches(4.35), Inches(1.1), Inches(0.06), theme["accent"])


_RENDERERS = {
    "title": _slide_title,
    "section": _slide_section,
    "bullets": _slide_bullets,
    "two_col": _slide_two_col,
    "quote": _slide_quote,
    "table": _slide_table,
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
