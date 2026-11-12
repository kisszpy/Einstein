"""套母版渲染器：以用户模板为基底「反用模板自身」生成演示文稿。

跨演示文稿复制版式在 python-pptx 里不可行（关系+母版+主题是一整套），
所以反过来：`Presentation(BytesIO(template_bytes))` → 清空示例页 →
逐页 `add_slide(layout)` 填占位符。主题/母版/版式继承全是真的，
DeckCraft 打开后按母版继承渲染也正常。

三级降级（逐级记入 degradations）：
1. layout_hint 名字模糊匹配 → 填 title/正文占位符；
2. 匹配不到 → 选占位符最多的版式；
3. 占位符缺失 → 绝对定位补文本框（absolute_textbox）。

中文字体必须 `run.font.name` + `qn("a:ea")/qn("a:cs")` 同设，
否则中文回落宋体（capabilities/ppt.py::_set_font 的既有结论）。
"""

from __future__ import annotations

import io
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.services.ppt_studio.outline import Outline, OutlinePage

EMU_PER_INCH = 914400.0
# 版心安全区（英寸）：绝对定位兜底时用，避免文字贴边
SAFE_MARGIN = 0.9

# 默认 16:9 尺寸兜底（幻灯片尺寸异常读不到时）
DEFAULT_SLIDE_SIZE = (13.333, 7.5)

_LAYOUT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cover": ("封面", "title slide", "标题幻灯片"),
    "section": ("节", "section", "章节"),
    "bullets": ("标题和内容", "title and content", "内容", "content"),
    "two_col": ("两栏", "two content", "比较", "comparison"),
    "quote": ("引用", "quote"),
    "blank": ("空白", "blank"),
}

_TITLE_PH = ("TITLE", "CENTER_TITLE")
_BODY_PH = ("BODY", "OBJECT", "SUBTITLE")


def _ph_type(ph) -> str:
    return str(ph.placeholder_format.type).split(".")[-1].split(" ")[0]


def _is_dark(hex_color: str) -> bool:
    """背景亮度判断：深色版式前景用浅色。"""
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        return False
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def _set_font(run, size: float, color: str, bold: bool, font_name: str) -> None:
    """字号 + 颜色 + 字体（含 a:ea/a:cs，中文才能不吃宋体兜底）。"""
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = font_name
    try:
        run.font.color.rgb = RGBColor.from_string((color or "000000").lstrip("#").upper())
    except ValueError:
        run.font.color.rgb = RGBColor.from_string("000000")
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", font_name)


def _fill_text_frame(frame, text: str, size: float, color: str, bold: bool,
                     font_name: str, first: bool = True) -> None:
    if first:
        # 清掉占位符继承的提示语（"单击此处编辑…"），从第一个空段落开始写
        frame.clear()
        paragraph = frame.paragraphs[0]
    else:
        paragraph = frame.add_paragraph()
    run = paragraph.add_run()
    run.text = text
    _set_font(run, size, color, bold, font_name)


class DeckRenderer:
    """把一份确认卡渲染成 pptx 字节。"""

    def __init__(self, palette: dict[str, str], fonts: dict[str, str]) -> None:
        self.palette = palette or {}
        self.fonts = fonts or {}
        self.major_font = (self.fonts.get("major_ea") or "Microsoft YaHei").strip()
        self.minor_font = (self.fonts.get("minor_ea") or "Microsoft YaHei").strip()
        # 幻灯片尺寸（英寸），render 时按基底演示文稿刷新
        self._slide_w_in, self._slide_h_in = DEFAULT_SLIDE_SIZE

    # ------------------------------------------------ 入口

    def render(self, template_bytes: bytes | None, outline: Outline) -> dict:
        """返回 {blob, total_slides, layouts_used, degradations, fidelity}。"""
        if template_bytes:
            prs = Presentation(io.BytesIO(template_bytes))
        else:
            prs = Presentation()
            # python-pptx 内置模板是 4:3；架构约定无模板时按 16:9 成稿
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)
        self._slide_w_in = round((prs.slide_width or 0) / EMU_PER_INCH, 3) or DEFAULT_SLIDE_SIZE[0]
        self._slide_h_in = round((prs.slide_height or 0) / EMU_PER_INCH, 3) or DEFAULT_SLIDE_SIZE[1]
        degradations: list[str] = []
        self._drop_existing_slides(prs)

        pages = outline.flatten_pages()
        layouts_used: list[str] = []
        for index, page in enumerate(pages):
            layout, degradation = self._pick_layout(prs, page, index, len(pages))
            if degradation:
                degradations.append(degradation)
            slide = prs.slides.add_slide(layout)
            layouts_used.append(layout.name or "未知版式")
            degradations.extend(self._fill_placeholder(slide, page, index))
            notes = slide.notes_slide.notes_text_frame
            notes.text = page.note or ""
            self._page_footer_sources(slide, page, outline)

        appendix = self._append_sources_slide(prs, outline)
        total = len(prs.slides._sldIdLst)  # noqa: SLF001 — 与清空示例页同一处内部结构

        blob = io.BytesIO()
        prs.save(blob)
        fidelity = {
            "score": 100 - min(len(degradations) * 3, 30),
            "issues": [],
            "degradations": degradations,
            "summary_zh": f"{total} 页生成完成" + (f"；{len(degradations)} 处降级" if degradations else ""),
        }
        fidelity["score"] = max(40, min(100, fidelity["score"]))
        return {
            "blob": blob.getvalue(),
            "total_slides": total,
            "layouts_used": layouts_used,
            "degradations": degradations,
            "fidelity": fidelity,
        }

    # ------------------------------------------------ 清空示例页

    def _drop_existing_slides(self, prs: Presentation) -> None:
        """清空模板自带示例页：drop_rel 断关系，再从 _sldIdLst 摘除。"""
        xml_slides = prs.slides._sldIdLst  # noqa: SLF001 — python-pptx 没有高层删除 API
        for sld_id in list(xml_slides):
            r_id = sld_id.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            if r_id:
                prs.part.drop_rel(r_id)
            xml_slides.remove(sld_id)

    # ------------------------------------------------ 版式选择

    def _layout_score(self, layout) -> int:
        placeholders = list(layout.placeholders)
        return len(placeholders)

    def _pick_layout(self, prs: Presentation, page: OutlinePage, index: int, total: int):
        """三级降级选版式，返回 (layout, degradation|None)。"""
        all_layouts = [l for master in prs.slide_masters for l in master.slide_layouts]
        if not all_layouts:
            return prs.slide_layouts[0] if len(prs.slide_layouts) else None, "模板没有可用版式（no_layout）"

        # 1) 前端选定 / layout_hint 关键词匹配
        for hint in (page.layout_resolved, page.layout):
            hint_text = (hint or "").strip().lower()
            if not hint_text or hint_text == "auto":
                continue
            keywords = _LAYOUT_KEYWORDS.get(hint_text, (hint_text,))
            for layout in all_layouts:
                name = (layout.name or "").lower()
                if any(keyword in name for keyword in keywords):
                    return layout, None

        # 封面/结尾页优先挑有标题占位符的版式；章节页同理
        want_title = page.layout in ("cover", "section", "end") or index == 0
        if want_title:
            titled = [l for l in all_layouts if any(_ph_type(p) in _TITLE_PH for p in l.placeholders)]
            if titled:
                return titled[0], None

        # 2) 占位符最多的版式（信息量兜底）
        body_layouts = [l for l in all_layouts if any(_ph_type(p) in _BODY_PH for p in l.placeholders)]
        pool = body_layouts or all_layouts
        best = max(pool, key=self._layout_score)
        degradation = None
        if not body_layouts:
            degradation = f"第 {index + 1}/{total} 页：模板版式无内容占位符，改用绝对定位文本框（absolute_textbox）"
        return best, degradation

    # ------------------------------------------------ 占位符填充

    def _fill_placeholder(self, slide, page: OutlinePage, index: int) -> list[str]:
        degradations: list[str] = []
        title_done = False
        body_filled = False

        for ph in slide.placeholders:
            ph_kind = _ph_type(ph)
            if ph_kind in _TITLE_PH and not title_done:
                size = 40 if len(page.title) <= 12 else 34 if len(page.title) <= 22 else 28
                _fill_text_frame(ph.text_frame, page.title, size, self._ink(), True, self.major_font)
                if page.kicker:
                    _fill_text_frame(ph.text_frame, page.kicker, 12, self._muted(), False,
                                     self.minor_font, first=False)
                title_done = True
            elif ph_kind in _BODY_PH and not body_filled:
                bullets = [b.strip() for b in page.bullets if b.strip()][:8]
                if len(page.bullets) > 8:
                    degradations.append(f"第 {index + 1} 页要点超出 8 条，已截断")
                size = self._bullet_size(bullets)
                first = True
                for bullet in bullets:
                    _fill_text_frame(ph.text_frame, bullet[:120], size, self._ink(), False,
                                     self.minor_font, first=first)
                    first = False
                body_filled = True

        if not title_done and page.title:
            self._absolute_fallback(slide, page, index)
            degradations.append(f"第 {index + 1} 页：版式缺标题占位符，绝对定位补文本框（absolute_textbox）")
        return degradations

    def _bullet_size(self, bullets: list[str]) -> int:
        """字号按条数与最长字数降级（20/18/16/14）。"""
        longest = max((len(b) for b in bullets), default=0)
        count = len(bullets)
        if count <= 4 and longest <= 34:
            return 20
        if count <= 6 and longest <= 46:
            return 18
        if count <= 8:
            return 16
        return 14

    def _ink(self) -> str:
        return self.palette.get("dk1") or "000000"

    def _muted(self) -> str:
        return self.palette.get("dk2") or "5D6B8A"

    # ------------------------------------------------ 绝对定位兜底

    def _absolute_fallback(self, slide, page: OutlinePage, index: int) -> None:
        """按主题色板与版心安全区补文本框（fallback: absolute_textbox）。"""
        width_in, height_in = self._slide_size(slide)
        box = slide.shapes.add_textbox(
            Inches(SAFE_MARGIN), Inches(1.2), Inches(width_in - SAFE_MARGIN * 2), Inches(1.1)
        )
        box.text_frame.word_wrap = True
        _fill_text_frame(box.text_frame, page.title, 30, self._ink(), True, self.major_font)

        bullets = [b.strip() for b in page.bullets if b.strip()][:8]
        if bullets:
            body = slide.shapes.add_textbox(
                Inches(SAFE_MARGIN), Inches(2.5),
                Inches(width_in - SAFE_MARGIN * 2), Inches(height_in - 3.4),
            )
            body.text_frame.word_wrap = True
            first = True
            for bullet in bullets:
                _fill_text_frame(body.text_frame, f"▪  {bullet[:120]}", 16, self._ink(),
                                 False, self.minor_font, first=first)
                first = False

    def _slide_size(self, _slide=None) -> tuple[float, float]:
        """幻灯片尺寸（英寸）；render 时已按基底刷新，异常时按 16:9 兜底。"""
        if self._slide_w_in <= 0 or self._slide_h_in <= 0:
            return DEFAULT_SLIDE_SIZE
        return self._slide_w_in, self._slide_h_in

    # ------------------------------------------------ 来源标注

    def _page_footer_sources(self, slide, page: OutlinePage, outline: Outline) -> None:
        """policy ∈ {page, both} 且该页有引用时，页脚上方 9pt 灰字「来源：1,2」。"""
        if outline.sources_policy not in ("page", "both") or not page.sources:
            return
        ids = "、".join(page.sources[i].id for i in range(len(page.sources)) if page.sources[i].id)
        if not ids:
            return
        width_in, height_in = self._slide_size(slide)
        box = slide.shapes.add_textbox(
            Inches(SAFE_MARGIN), Inches(height_in - 0.62), Inches(width_in - SAFE_MARGIN * 2), Inches(0.3)
        )
        box.text_frame.word_wrap = False
        _fill_text_frame(box.text_frame, f"来源：{ids}", 9, self._muted(), False, self.minor_font)

    def _append_sources_slide(self, prs: Presentation, outline: Outline) -> int:
        """来源附录页（policy ∈ {appendix, both}）；返回是否追加了 1 页。"""
        if not outline.has_appendix():
            return 0
        layouts = [l for master in prs.slide_masters for l in master.slide_layouts]
        titled = [l for l in layouts if any(_ph_type(p) in _TITLE_PH for p in l.placeholders)]
        layout = titled[0] if titled else (layouts[0] if layouts else None)
        if layout is None:
            return 0
        slide = prs.slides.add_slide(layout)
        sources = outline.appendix_sources()
        for ph in slide.placeholders:
            ph_kind = _ph_type(ph)
            if ph_kind in _TITLE_PH:
                _fill_text_frame(ph.text_frame, "参考来源", 32, self._ink(), True, self.major_font)
            elif ph_kind in _BODY_PH:
                first = True
                for i, ref in enumerate(sources, start=1):
                    label = f"{i}. {ref.title or ref.url or ref.kb_name}"
                    _fill_text_frame(ph.text_frame, label[:110], 14, self._ink(), False,
                                     self.minor_font, first=first)
                    first = False
        return 1
