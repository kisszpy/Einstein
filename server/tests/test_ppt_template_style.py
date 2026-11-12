"""上传模板 → 设计家族：抽取口径与端到端渲染。

fixture 用 python-pptx 现造模板，而不是塞一份二进制进仓库：
每个用例只改一个变量（底色 / 品牌色 / 比例 / 字体），失败时能一眼看出是哪条口径塌了。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.util import Emu, Inches, Pt

from app.services.ppt_studio import designs, template_style, theme_pack
from app.services.ppt_studio.htmlkit import color, compose, lint, measure, pptx_writer
from app.services.ppt_studio.htmlkit import tokens as tk
from app.services.ppt_studio.outline import Outline

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _patch_theme(prs: Presentation, *, accent1: str, accent2: str, latin: str, ea: str) -> None:
    part = next(
        p for p in prs.part.package.iter_parts() if str(p.partname).startswith("/ppt/theme/theme")
    )
    root = etree.fromstring(part.blob)
    scheme = root.find(f".//{{{_A}}}clrScheme")
    for name, value in (("accent1", accent1), ("accent2", accent2)):
        node = scheme.find(f"{{{_A}}}{name}")
        for child in list(node):
            node.remove(child)
        etree.SubElement(node, f"{{{_A}}}srgbClr", val=value)
    fonts = root.find(f".//{{{_A}}}fontScheme")
    for group, tag, value in (("majorFont", "latin", latin), ("majorFont", "ea", ea),
                              ("minorFont", "latin", latin), ("minorFont", "ea", ea)):
        node = fonts.find(f"{{{_A}}}{group}/{{{_A}}}{tag}")
        node.set("typeface", value)
    part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _rect(slide, left, top, width, height, hexed, *, shape=MSO_SHAPE.RECTANGLE, adj=None):
    box = slide.shapes.add_shape(shape, Inches(left), Inches(top), Inches(width), Inches(height))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor.from_string(hexed)
    box.line.fill.background()
    if adj is not None and box.adjustments:
        box.adjustments[0] = adj
    return box


def _text(slide, left, top, width, height, text, hexed, size=18, *, font=None):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    box.text_frame.word_wrap = True
    box.text_frame.text = text
    run = box.text_frame.paragraphs[0].runs[0]
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(hexed)
    if font:
        run.font.name = font
    return box


def _template(
    *,
    width_in: float = 13.333,
    height_in: float = 7.5,
    cover_bg: str = "0E1B2E",
    content_bg: str = "FFFFFF",
    brand: str = "1E6FD9",
    second: str = "E8873C",
    theme_accent1: str = "4F81BD",
    latin: str = "Georgia",
    ea: str = "微软雅黑",
    cards: int = 3,
    rounded: bool = True,
    gradient_cover: bool = False,
) -> bytes:
    prs = Presentation()
    prs.slide_width = Emu(int(width_in * 914400))
    prs.slide_height = Emu(int(height_in * 914400))
    _patch_theme(prs, accent1=theme_accent1, accent2=second, latin=latin, ea=ea)

    cover = prs.slides.add_slide(prs.slide_layouts[0])
    if gradient_cover:
        # python-pptx 没有渐变 API，直接写 bgPr：真实模板的封面多半就是渐变。
        # bg 必须是 cSld 的第一个子元素，顺序错了 PowerPoint 会判文件损坏。
        csld = cover._element.find(f"{{{_P}}}cSld")
        bg = etree.Element(f"{{{_P}}}bg")
        csld.insert(0, bg)
        props = etree.SubElement(bg, f"{{{_P}}}bgPr")
        grad = etree.SubElement(props, f"{{{_A}}}gradFill")
        stops = etree.SubElement(grad, f"{{{_A}}}gsLst")
        for pos, hexed in ((0, "050A18"), (50000, cover_bg), (100000, brand)):
            gs = etree.SubElement(stops, f"{{{_A}}}gs", pos=str(pos))
            etree.SubElement(gs, f"{{{_A}}}srgbClr", val=hexed)
        etree.SubElement(props, f"{{{_A}}}effectLst")
    else:
        cover.background.fill.solid()
        cover.background.fill.fore_color.rgb = RGBColor.from_string(cover_bg)
    _text(cover, 0.8, 2.4, width_in - 1.6, 1.4, "年度经营汇报", "FFFFFF", 40)
    _rect(cover, 0.8, 4.1, 2.4, 0.06, brand)

    content = prs.slides.add_slide(prs.slide_layouts[1])
    content.background.fill.solid()
    content.background.fill.fore_color.rgb = RGBColor.from_string(content_bg)
    _rect(content, 0, 0, width_in, 0.14, brand)
    _text(content, 0.7, 0.5, width_in - 1.4, 0.8, "业务概览", "1F2937", 28)
    shape = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    card_w = (width_in - 1.4 - 0.4 * (cards - 1)) / max(cards, 1)
    for i in range(cards):
        left = 0.7 + i * (card_w + 0.4)
        _rect(content, left, 2.0, card_w, 2.6, second if i == 1 else brand,
              shape=shape, adj=0.08 if rounded else None)

    third = prs.slides.add_slide(prs.slide_layouts[1])
    third.background.fill.solid()
    third.background.fill.fore_color.rgb = RGBColor.from_string(content_bg)
    _text(third, 0.7, 0.5, width_in - 1.4, 0.8, "下一步计划", "1F2937", 28)
    _rect(third, 0.7, 1.6, width_in - 1.4, 0.04, "D0D5DD")

    blob = io.BytesIO()
    prs.save(blob)
    return blob.getvalue()


def _extract(raw: bytes, name: str = "示例公司模板") -> dict:
    parsed = theme_pack.parse_theme_pack(raw, f"{name}.pptx")
    return parsed["style"]


def _deep(shapes):
    """pptx_writer 会把整页装进组合形状，GroupShape.has_text_frame 恒为 False。"""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _deep(shape.shapes)


def _outline() -> Outline:
    return Outline.model_validate({
        "title": "智能制造年度汇报",
        "subtitle": "从设备联网到经营决策",
        "audience": "集团管理层",
        "goal": "对齐明年投入节奏",
        "sections": [
            {"title": "开场", "pages": [
                {"title": "智能制造年度汇报", "layout": "cover", "bullets": ["覆盖 12 条产线"]},
            ]},
            {"title": "现状", "pages": [
                {"title": "设备联网现状", "layout": "stats", "bullets": [
                    "联网率 78%：较去年提升 21 个百分点",
                    "停机时长 4.2 小时：同比下降三成",
                    "人工巡检 1200 次：仍集中在夜班",
                ]},
                {"title": "三个瓶颈", "layout": "bullets", "bullets": [
                    "数据口径不统一，跨车间无法横向比较",
                    "告警噪声大，值班同事开始忽略提示",
                    "报表滞后一天，决策只能靠经验补位",
                ]},
            ]},
            {"title": "收尾", "pages": [
                {"title": "谢谢", "layout": "end", "bullets": []},
            ]},
        ],
        "style": {"theme_pack_id": ""},
    })


# ---------------------------------------------------------------- 明暗与主色

def test_mode_follows_content_pages_not_the_cover():
    """封面深、内页浅的公司模板不在少数；成稿多数页是内页，明暗必须由内页定。"""
    style = _extract(_template(cover_bg="0E1B2E", content_bg="FFFFFF"))
    report = style["report"]
    assert report["mode"] == "light"
    assert color.luminance(report["background"]) > 0.6
    assert any("封面为深底" in note for note in report["notes"])


def test_all_dark_template_stays_dark():
    style = _extract(_template(cover_bg="0B1223", content_bg="121A2B"))
    assert style["report"]["mode"] == "dark"
    assert style["preset"]["family"]["mode"] == "dark"
    assert not any("统一按内页" in note for note in style["report"]["notes"])


def test_sampled_brand_color_beats_the_untouched_office_theme():
    """很多模板压根没改主题，accent1 还是 Office 默认的 4F81BD。"""
    style = _extract(_template(brand="1E6FD9", theme_accent1="4F81BD"))
    assert style["report"]["accent"] == "1E6FD9"
    assert style["preset"]["palette"]["accent"] == "1E6FD9"


def test_secondary_color_comes_from_the_second_brand_hue():
    style = _extract(_template(brand="1E6FD9", second="E8873C"))
    assert color.hue_gap(style["report"]["accent2"], "E8873C") < 25


def test_gray_only_template_still_produces_a_usable_accent():
    """全灰模板不能生成一份没有强调色的稿子。"""
    style = _extract(_template(brand="8A8A8A", second="B0B0B0", theme_accent1="A5A5A5"))
    accent = style["report"]["accent"]
    assert color.saturation(accent) >= 0.2
    assert len(style["report"]["chart"]) == 7


# ---------------------------------------------------------------- 色板与图表色

@pytest.mark.parametrize("cover,content", [("0E1B2E", "FFFFFF"), ("0B1223", "121A2B")])
def test_palette_text_pairs_are_readable(cover, content):
    style = _extract(_template(cover_bg=cover, content_bg=content))
    tokens = tk.resolve_tokens(style["preset"])
    colors = tokens["colors"]
    assert color.contrast(colors["ink"], colors["bg"]) >= 7.0
    assert color.contrast(colors["muted"], colors["bg"]) >= 4.5
    assert color.contrast(colors["faint"], colors["bg"]) >= 4.5
    assert color.contrast(colors["accent_ink"], colors["bg"]) >= 4.5
    assert color.contrast(colors["accent_ink"], colors["accent_soft"]) >= 4.5
    assert color.contrast(colors["on_accent"], colors["accent"]) >= 4.5


def test_chart_ramp_is_seven_separable_hues_on_the_content_background():
    style = _extract(_template())
    tokens = tk.resolve_tokens(style["preset"])
    ramp = tokens["chart"]
    assert len(ramp) == 7
    bg = tokens["colors"]["bg"]
    for hue in ramp:
        assert color.contrast(hue, bg) >= 1.6, hue
    for i in range(len(ramp) - 1):
        for j in range(i + 1, len(ramp)):
            gap = color.hue_gap(ramp[i], ramp[j])
            dl = abs(color.lightness(ramp[i]) - color.lightness(ramp[j]))
            # 末位是中性色，色相无意义，只能靠明度分开
            assert gap >= 18 or dl >= 0.09, (ramp[i], ramp[j], gap, dl)


def test_cover_gradient_crosses_real_hues():
    style = _extract(_template())
    stops = style["preset"]["family"]["cover_stops"]
    assert len(stops) >= 3
    assert color.distance(stops[0], stops[-1]) >= 0.1


def test_template_gradient_background_is_reused_as_cover_stops():
    style = _extract(_template(gradient_cover=True, content_bg="0B1223", cover_bg="101B33"))
    stops = style["report"]["cover_stops"]
    assert "050A18" in stops


def test_dark_cover_gradient_never_survives_into_a_light_deck():
    """深封面 + 浅内页：基调由内页定，照搬深封面会把浅墨标题压成 1.0:1。"""
    style = _extract(_template(gradient_cover=True, cover_bg="0E1B2E", content_bg="FFFFFF"))
    report = style["report"]
    assert report["mode"] == "light"
    stops = report["cover_stops"]
    assert "050A18" not in stops
    tokens = tk.resolve_tokens(style["preset"])
    ink = tokens["colors"]["ink"]
    for stop in stops:
        assert color.contrast(ink, stop) >= 3.0, (stop, color.contrast(ink, stop))
    assert any("封面为深底" in note for note in report["notes"])


# ---------------------------------------------------------------- 字体

def test_chinese_font_alias_resolves_to_the_installed_family():
    style = _extract(_template(ea="微软雅黑", latin="Georgia"))
    assert style["report"]["fonts"]["body"] == "Microsoft YaHei"
    assert style["report"]["fonts"]["display"] == "Georgia"
    assert not any("微软雅黑" in note for note in style["report"]["notes"])


def test_uninstalled_font_is_substituted_and_recorded():
    """写一个本机没有的字体名，DOM 实测行宽与 pptx 断行就会错位——必须替换并留痕。"""
    style = _extract(_template(ea="阿里巴巴普惠体 3.0", latin="Acme Sans"))
    fonts = style["report"]["fonts"]
    assert fonts["body"] == "Microsoft YaHei"
    assert fonts["display"] in ("Microsoft YaHei", "Inter")
    assert any("阿里巴巴普惠体" in note for note in style["report"]["notes"])
    assert fonts["source_body"] == "阿里巴巴普惠体 3.0"


def test_latin_only_font_is_never_used_for_chinese_body():
    style = _extract(_template(ea="Arial", latin="Arial"))
    assert style["report"]["fonts"]["body"] == "Microsoft YaHei"
    assert any("不含中文字形" in note for note in style["report"]["notes"])


# ---------------------------------------------------------------- 几何

def test_four_three_template_keeps_its_ratio_end_to_end(tmp_path):
    raw = _template(width_in=10.0, height_in=7.5)
    style = _extract(raw)
    assert style["report"]["canvas"] == [960, 720]
    assert style["report"]["ratio"] == "4:3"
    assert not any("物理尺寸有变化" in note for note in style["report"]["notes"])

    tokens = tk.resolve_tokens(style["preset"])
    slides = compose.compose_deck(_outline(), tokens)
    measured = measure.measure(slides[:2], tmp_path, canvas=(960, 720))
    assert all(m["size"]["w"] == 960 for m in measured)
    out = tmp_path / "deck.pptx"
    pptx_writer.write_deck(measured, tokens, out)
    prs = Presentation(str(out))
    assert (prs.slide_width, prs.slide_height) == (9144000, 6858000)


def test_portrait_template_reports_the_physical_size_change():
    style = _extract(_template(width_in=7.5, height_in=10.0))
    assert style["report"]["canvas"][0] < style["report"]["canvas"][1]
    assert any("物理尺寸有变化" in note for note in style["report"]["notes"])


def test_rounded_cards_drive_radius_and_glass_motif():
    style = _extract(_template(cards=4, rounded=True))
    assert style["report"]["radius_px"] > 0
    assert style["report"]["motif"] == "glass"


def test_square_cards_do_not_invent_a_corner_radius():
    style = _extract(_template(cards=4, rounded=False))
    assert style["report"]["radius_px"] == 14  # 没有圆角证据时用引擎默认值
    assert style["report"]["motif"] != "glass"


def test_content_margin_comes_from_placeholder_geometry():
    style = _extract(_template())
    assert 44 <= style["report"]["margin_px"] <= 96
    tokens = tk.resolve_tokens(style["preset"])
    assert tokens["margin"] == style["report"]["margin_px"]


# ---------------------------------------------------------------- 渲染闭环

@pytest.mark.parametrize("cover,content", [("0E1B2E", "FFFFFF"), ("0B1223", "121A2B")])
def test_extracted_style_renders_a_clean_editable_deck(tmp_path, cover, content):
    style = _extract(_template(cover_bg=cover, content_bg=content))
    tokens = tk.resolve_tokens(style["preset"], tk.Knobs.from_style({"accent": "#7A2FE0"}))
    slides = compose.compose_deck(_outline(), tokens)
    measured = measure.measure(slides, tmp_path, canvas=(tokens["canvas_w"], tokens["canvas_h"]))
    for item in measured:
        report = lint.inspect(item, tokens, layout=slides[int(item["index"])].kind)
        assert not report.errors, [i.detail for i in report.errors]
    out = tmp_path / "deck.pptx"
    pptx_writer.write_deck(measured, tokens, out)
    prs = Presentation(str(out))
    shapes = list(_deep(prs.slides[1].shapes))
    assert [s for s in shapes if s.has_text_frame and s.text_frame.text.strip()], "成稿必须可编辑，不是整页截图"
    assert not [s for s in shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]


def test_theme_missing_template_still_extracts_a_style():
    """去掉 theme1.xml 的包也要能生成，不能整条链路报错。"""
    raw = _template()
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename.startswith("ppt/theme/theme"):
                continue
            dst.writestr(item, src.read(item.filename))
    parsed = theme_pack.parse_theme_pack(buf.getvalue(), "无主题.pptx")
    assert parsed["meta"]["theme_missing"] is True
    assert parsed["style"]["preset"]["palette"]["accent"]
    assert parsed["style"]["report"]["mode"] in ("dark", "light")


def test_style_of_backfills_packs_uploaded_before_extraction_existed(tmp_path, monkeypatch):
    raw = _template()
    parsed = theme_pack.parse_theme_pack(raw, "老包.pptx")
    legacy = {
        "id": "tp_legacy", "name": "老包", "workspace_id": "ws", "owner_id": "u",
        "palette": parsed["palette"], "fonts": parsed["fonts"], "layouts": parsed["layouts"],
        "meta": parsed["meta"], "source_path": "ppt_templates/ws/tp_legacy.pptx",
    }
    target = tmp_path / "ppt_templates" / "ws" / "tp_legacy.pptx"
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    monkeypatch.setattr(theme_pack, "_DATA_DIR", tmp_path)
    saved: dict = {}
    monkeypatch.setattr(theme_pack, "_persist_style", lambda pid, style: saved.update({pid: style}))

    style = theme_pack.style_of(legacy)
    assert style["report"]["version"] == template_style.STYLE_VERSION
    assert style["preset"]["canvas"] == [1280, 720]
    assert saved["tp_legacy"]["report"]["accent"] == style["report"]["accent"]


def test_style_of_reuses_the_stored_extraction(tmp_path, monkeypatch):
    raw = _template()
    style = _extract(raw)
    pack = {"id": "tp_x", "style": style, "palette": {}, "fonts": {}, "meta": {}}
    monkeypatch.setattr(theme_pack, "source_bytes", lambda _p: pytest.fail("已抽过就不该再读源文件"))
    assert theme_pack.style_of(pack) is style


def test_extraction_never_breaks_upload_for_a_broken_presentation(monkeypatch):
    raw = _template()
    monkeypatch.setattr(template_style, "extract", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    parsed = theme_pack.parse_theme_pack(raw, "异常.pptx")
    assert parsed["style"]["report"]["notes"]
    assert parsed["style"]["preset"]["palette"]["accent"]


def test_builtin_presets_are_untouched_by_the_margin_override():
    """内置 preset 没有 margin_px，版心必须还是密度表的原值。"""
    for preset in designs.all_presets():
        assert tk.resolve_tokens(preset)["margin"] == 64
        assert tk.resolve_tokens(preset, tk.Knobs.from_style({"density": "loose"}))["margin"] == 76


# ------------------------------------------------- 真实模板才会踩到的四个坑
# 上面用的是「一个变量一页」的合成模板；下面这份按真实公司模板的样子造：
# 空白版式 + 手摆文本框、run 上单独设字体、大量近白正文墨色、品牌副色只出现在细条上。

def _real_world_template(*, painted_cards: bool = True, body_font: str | None = "阿里巴巴普惠体 3.0") -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(int(10 * 914400)), Emu(int(7.5 * 914400))
    _patch_theme(prs, accent1="4F81BD", accent2="ED7D31", latin="Georgia", ea="思源宋体")
    bg, brand, second, ink = "101418", "00C2A8", "FF3D8B", "F2F5F7"

    cover = prs.slides.add_slide(prs.slide_layouts[6])
    cover.background.fill.solid()
    cover.background.fill.fore_color.rgb = RGBColor.from_string(bg)
    _rect(cover, 0, 0, 0.14, 7.5, brand)
    _text(cover, 0.85, 2.2, 8.3, 1.4, "季度技术评审", ink, 40, font=body_font)
    _rect(cover, 0.85, 4.7, 1.8, 0.05, second)

    for title in ("现状盘点", "架构演进", "下季度目标"):
        page = prs.slides.add_slide(prs.slide_layouts[6])
        page.background.fill.solid()
        page.background.fill.fore_color.rgb = RGBColor.from_string(bg)
        _rect(page, 0, 0, 0.14, 7.5, brand)
        _text(page, 0.85, 0.6, 8.3, 0.8, title, ink, 28, font=body_font)
        _rect(page, 0.85, 1.5, 8.3, 0.02, "2A333B")
        for i in range(2):
            top = 1.95 + i * 2.05
            if painted_cards:
                _rect(page, 0.85, top, 8.3, 1.75, "181F25")
            _rect(page, 0.85, top, 0.05, 1.75, second if i else brand)
            _text(page, 1.15, top + 0.2, 0.9, 0.8, f"{i + 1:02d}", second if i else brand, 36, font=body_font)
            _text(page, 2.15, top + 0.28, 6.7, 1.2,
                  "服务网格下沉，统一流量治理与可观测口径，先在三条业务线灰度",
                  ink, 16, font=body_font)
    blob = io.BytesIO()
    prs.save(blob)
    return blob.getvalue()


def test_near_white_body_ink_never_becomes_the_secondary_brand_color():
    """HLS 饱和度在近白处虚高（F2F5F7 的 s≈0.24），只看它会把正文墨色当品牌副色。"""
    report = _extract(_real_world_template())["report"]
    assert report["accent"] == "00C2A8"
    # 副色必须是那条洋红细条，而不是正文的 F2F5F7、也不是 Office 出厂的 4F81BD
    assert report["accent2"] == "FF3D8B"
    assert color.chroma(report["accent2"]) >= 0.5


def test_small_sampled_brand_accent_beats_the_untouched_office_theme():
    """公司模板多半只改版面不动主题：accent1 还是 4F81BD，不能让它盖掉真副色。"""
    report = _extract(_real_world_template())["report"]
    assert "4F81BD" not in (report["accent"], report["accent2"])
    assert "4F81BD" not in report["chart"][:2]


def test_run_level_font_wins_over_the_theme_and_layout_prompt_text():
    """字体写在 run 上才是模板实际在用的；版式里上千个英文提示字符不能参与投票。"""
    report = _extract(_real_world_template())["report"]
    assert report["fonts"]["source_body"] == "阿里巴巴普惠体 3.0"
    # 缺失字体回落模板自己声明的主题字族（思源宋体→Noto Serif SC），保住衬线气质
    assert report["fonts"]["body"] == "Noto Serif SC"
    hits = [n for n in report["notes"] if "阿里巴巴普惠体 3.0" in n]
    assert len(hits) == 1
    assert "Noto Serif SC" in hits[0]


def test_installed_run_font_is_used_verbatim():
    report = _extract(_real_world_template(body_font="微软雅黑"))["report"]
    assert report["fonts"]["source_body"] == "微软雅黑"
    assert report["fonts"]["body"] == "Microsoft YaHei"
    assert not any("微软雅黑" in n for n in report["notes"])


def test_empty_placeholders_do_not_invent_a_glass_motif():
    """python-pptx 自带十几个空占位符版式，把它们当卡片会让任何模板都变玻璃拟态。"""
    report = _extract(_real_world_template(painted_cards=False))["report"]
    assert report["motif"] != "glass"
    assert report["radius_px"] == 14
    assert report["evidence"]["cards"] == 0


def test_content_margin_follows_the_slides_not_the_unused_default_layouts():
    """模板用空白版式手摆文本框时，版心要量文本框，而不是没用过的默认占位符。"""
    report = _extract(_real_world_template())["report"]
    assert report["margin_px"] == 82  # 0.85in × 96dpi
    assert report["canvas"] == [960, 720]