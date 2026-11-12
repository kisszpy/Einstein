"""HTML 幻灯片套件：令牌 clamp、逐页体检、修复阶梯、原生形状写出。

这些用例全部**不启浏览器**——体检与写出都是纯函数，喂手工构造的实测几何
即可覆盖。真实 Chromium 往返由 .workbuddy/ppt-html/{probe,render,stress}.py
探针跑（本机需要 node + playwright-core），不进 CI。
"""

from __future__ import annotations

import io
import json
import re

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

from app.services.ppt_studio import designs
from app.services.ppt_studio.htmlkit import color, compose, lint, measure, pptx_writer, stylesheet, tokens as tk
from app.services.ppt_studio.outline import Outline


def _tokens(family: str = "midnight", **knobs) -> dict:
    preset = designs.resolve_id(f"builtin:{family}") or designs.default_preset()
    return tk.resolve_tokens(preset, tk.Knobs.from_style(knobs))


def _outline() -> Outline:
    return Outline.model_validate({
        "title": "智能制造三年跃迁",
        "subtitle": "年度战略汇报",
        "style": {"theme_pack_id": "builtin:midnight"},
        "sections": [
            {"title": "开场", "pages": [{"title": "智能制造三年跃迁", "layout": "cover"}]},
            {"title": "成果", "pages": [
                {"title": "关键指标", "layout": "stat",
                 "bullets": ["3200 台设备联网", "64% 换线时间压缩", "1.5 小时平均换线"]},
                {"title": "各产线设备数", "layout": "chart",
                 "bullets": ["一号车间 1240 台", "二号车间 860 台", "三号车间 620 台"]},
                {"title": "落地主线", "layout": "cards",
                 "bullets": ["数据底座：统一 17 套系统，查询延迟 0.4 秒",
                             "工艺闭环：回滚耗时压到 3 分钟",
                             "人机协同：沉淀 240 条规则"]},
            ]},
            {"title": "收尾", "pages": [{"title": "谢谢", "layout": "end"}]},
        ],
        "sources": [{"id": "s1", "kind": "web", "title": "行业报告", "url": "https://example.com/a"}],
    }).normalize()


def _text_node(node_id: str, x: float, y: float, w: float, h: float, text: str,
               *, size: float = 23.0, fg: str = "rgb(247, 250, 255)", cls: str = "body") -> dict:
    return {
        "id": node_id, "kind": "text", "tag": "div", "className": cls,
        "rect": {"x": x, "y": y, "w": w, "h": h},
        "style": {"bg": "rgba(0, 0, 0, 0)", "bgImage": "none", "opacity": 1},
        "lines": [{"x": x, "y": y, "w": w, "h": h, "runs": [
            {"text": text, "fontSize": size, "fontWeight": "400", "color": fg,
             "fontFamily": '"Microsoft YaHei", sans-serif', "letterSpacing": 0}
        ]}],
        "depth": 1,
    }


def _shape_node(node_id: str, x: float, y: float, w: float, h: float, bg: str,
                *, cls: str = "card", gradient: str = "none", radius=None) -> dict:
    return {
        "id": node_id, "kind": "shape", "tag": "div", "className": cls,
        "rect": {"x": x, "y": y, "w": w, "h": h},
        "style": {"bg": bg, "bgImage": gradient, "opacity": 1,
                  "radius": radius or [0, 0, 0, 0], "borderWidth": [0, 0, 0, 0],
                  "borderColor": [""] * 4, "borderStyle": ["none"] * 4},
        "depth": 0,
    }


def _measured(nodes: list[dict], *, index: int = 0, diagnostics=None) -> dict:
    root = _shape_node("root", 0, 0, tk.CANVAS_W, tk.CANVAS_H, "rgb(11, 18, 35)", cls="slide")
    return {"index": index, "nodes": [root] + nodes, "diagnostics": diagnostics or [],
            "size": {"w": tk.CANVAS_W, "h": tk.CANVAS_H}}


# ---------------------------------------------------------------- 令牌


def test_knobs_clamp_extremes():
    knobs = tk.Knobs.from_style({
        "type_scale": 99, "radius": -20, "whitespace": 5, "density": "wild",
        "graphic": "nope", "accent": "not-a-color", "seed": -3,
    })
    assert knobs.type_scale == 1.14
    assert knobs.radius == 0
    assert knobs.whitespace == 1.25
    assert knobs.density == "normal"
    assert knobs.graphic == "auto"
    assert knobs.accent == ""
    assert knobs.seed == 0


def test_outline_style_knobs_survive_normalize():
    outline = Outline.model_validate({
        "title": "x", "style": {"type_scale": 0.1, "radius": 900, "accent": "#E11D63", "seed": 7},
        "sections": [{"title": "s", "pages": [{"title": "p"}]}],
    }).normalize()
    assert outline.style.type_scale == 0.88
    assert outline.style.radius == 28
    assert outline.style.accent == "#E11D63"
    assert outline.style.seed == 7


def test_secondary_text_meets_contrast_on_every_family():
    """页脚/来源这类次要文字也必须能读——上一版只有 3.6:1，投影看不见。"""
    for family in ("midnight", "graphite", "ocean", "sunset", "forest", "ivory"):
        tokens = _tokens(family)
        colors = tokens["colors"]
        assert color.contrast(colors["faint"], colors["bg"]) >= 4.4, family
        assert color.contrast(colors["accent_ink"], colors["bg"]) >= 4.4, family
        assert color.contrast(colors["accent_ink"], colors["accent_soft"]) >= 4.4, family


def test_accent_override_reaches_css_vars():
    tokens = _tokens("midnight", accent="#E11D63")
    assert tokens["colors"]["accent"] == "E11D63"
    assert tokens["css_vars"]["--c-accent"] == "#E11D63"


# ---------------------------------------------------------------- 合成


def test_compose_produces_one_document_per_page_plus_appendix():
    outline = _outline()
    tokens = _tokens()
    slides = compose.compose_deck(outline, tokens)
    assert len(slides) == outline.total_slides()
    assert slides[-1].kind == "appendix"
    for slide in slides:
        assert slide.html.startswith("<!doctype html>")
        assert 'class="slide"' in slide.html
        assert "--c-accent" in slide.html


def test_chart_kind_follows_semantics_not_percent_sign():
    """月度合格率是时间序列，不能因为带 % 就画成饼图。"""
    monthly = [("1 月", 88.2, "%"), ("3 月", 90.6, "%"), ("5 月", 92.1, "%")]
    share = [("硬件", 42.0, "%"), ("软件", 26.0, "%"), ("服务", 32.0, "%")]
    counts = [("一号车间", 1240.0, "台"), ("二号车间", 860.0, "台")]
    assert compose._chart_kind(monthly) == "column"
    assert compose._chart_kind(share) == "donut"
    assert compose._chart_kind(counts) == "bar"


def test_page_override_only_touches_named_page():
    outline = _outline()
    tokens = _tokens()
    base = compose.compose_deck(outline, tokens)
    patched = compose.compose_deck(outline, tokens, overrides={
        2: compose.PageOverride(layout="bullets", scale=0.93),
    })
    assert patched[2].kind == "bullets"
    assert "--t-h1:" in patched[2].html
    assert base[1].html == patched[1].html
    assert base[3].html == patched[3].html


# ---------------------------------------------------------------- 体检


def test_lint_flags_browser_clipping_as_error():
    report = lint.inspect(_measured([], diagnostics=[
        {"ruleId": "text-clipped", "detail": "body 内容被裁切", "className": "body"},
    ]), _tokens(), layout="bullets")
    assert not report.ok
    assert report.errors[0].rule == "text-clipped"


def test_lint_flags_overlapping_text_blocks():
    nodes = [
        _text_node("n1", 100, 200, 400, 40, "第一块文字"),
        _text_node("n2", 120, 210, 400, 40, "第二块文字"),
    ]
    report = lint.inspect(_measured(nodes), _tokens(), layout="bullets")
    assert any(i.rule == "text-overlap" and i.severity == "error" for i in report.issues)


def test_lint_accepts_adjacent_lines_of_same_block():
    node = _text_node("n1", 100, 200, 400, 40, "第一行")
    node["lines"].append({"x": 100, "y": 240, "w": 400, "h": 40, "runs": [
        {"text": "第二行", "fontSize": 23, "fontWeight": "400",
         "color": "rgb(247, 250, 255)", "fontFamily": "Microsoft YaHei", "letterSpacing": 0}]})
    report = lint.inspect(_measured([node]), _tokens(), layout="bullets")
    assert not any(i.rule == "text-overlap" for i in report.issues)


def test_lint_flags_out_of_canvas_but_not_motif_bleed():
    nodes = [
        _text_node("n1", 1200, 200, 400, 40, "越界文字"),
        _shape_node("n2", 1100, -80, 300, 300, "rgb(79, 107, 246)", cls="m-orb"),
    ]
    report = lint.inspect(_measured(nodes), _tokens(), layout="bullets")
    offenders = [i.node_id for i in report.issues if i.rule == "out-of-canvas"]
    assert offenders == ["n1"]


def test_lint_flags_tiny_type():
    report = lint.inspect(_measured([_text_node("n1", 100, 200, 300, 20, "小字", size=11)]),
                          _tokens(), layout="bullets")
    assert any(i.rule == "type-too-small" and i.severity == "error" for i in report.issues)


def test_lint_contrast_uses_real_backdrop_not_page_bg():
    """浅底浅字：页面是深底，但这块文字落在浅色卡片上。"""
    nodes = [
        _shape_node("n1", 80, 180, 600, 120, "rgb(240, 240, 240)"),
        _text_node("n2", 100, 200, 400, 40, "浅色上的浅字", fg="rgb(230, 230, 230)"),
    ]
    report = lint.inspect(_measured(nodes), _tokens(), layout="bullets")
    assert any(i.rule == "low-contrast" and i.severity == "error" for i in report.issues)


def test_lint_samples_gradient_at_the_actual_point():
    """封面左上角是渐变最暗端，拿中点色判对比度会误报。"""
    gradient = "linear-gradient(128deg, rgb(5, 10, 24) 0%, rgb(36, 64, 143) 100%)"
    nodes = [
        _shape_node("n1", 0, 0, tk.CANVAS_W, tk.CANVAS_H, "rgba(0, 0, 0, 0)",
                    cls="slide", gradient=gradient),
        _text_node("n2", 64, 64, 300, 24, "STRATEGY 2026", size=16,
                   fg="rgb(79, 107, 246)", cls="kicker"),
    ]
    # 左上角实测 4.2:1，渐变中点只有 3.3:1——取中点就会把这页误判成不合格
    corner = lint.inspect(_measured(nodes), _tokens(), layout="cover")
    assert corner.min_contrast > 4.0, corner.issues
    # 同一颜色挪到渐变亮端就该被判不达标
    nodes[1] = _text_node("n3", 980, 660, 260, 24, "THANK YOU", size=16,
                          fg="rgb(79, 107, 246)", cls="kicker")
    bright_end = lint.inspect(_measured(nodes), _tokens(), layout="cover")
    assert bright_end.min_contrast < 3.0
    assert any(i.rule == "low-contrast" for i in bright_end.issues)


def test_gradient_without_angle_defaults_to_downward():
    """Chromium 会省略 180deg，柱状图竖向渐变必须照样解析出来。"""
    tokens = _tokens()
    top = lint._fill_of({"bgImage": "linear-gradient(rgb(79, 107, 246) 0%, rgb(125, 145, 248) 100%)",
                         "opacity": 1}, {"x": 0, "y": 0, "w": 100, "h": 200}, 50, 0)
    bottom = lint._fill_of({"bgImage": "linear-gradient(rgb(79, 107, 246) 0%, rgb(125, 145, 248) 100%)",
                            "opacity": 1}, {"x": 0, "y": 0, "w": 100, "h": 200}, 50, 200)
    assert top[0] == "4F6BF6"
    assert bottom[0] == "7D91F8"
    assert tokens["colors"]["bg"]  # 令牌仍然可用


def test_fill_ratio_separates_sparse_from_dense():
    sparse = lint.inspect(_measured([_text_node("n1", 100, 200, 200, 30, "一行")]),
                          _tokens(), layout="bullets")
    dense_nodes = [_text_node(f"n{i}", 70, 70 + i * 40, 1100, 34, "密集文字" * 12)
                   for i in range(14)]
    dense = lint.inspect(_measured(dense_nodes), _tokens(), layout="bullets")
    assert sparse.fill < lint.FILL_SPARSE
    assert dense.fill > sparse.fill
    assert any(i.rule == "sparse" for i in sparse.issues)


def test_sparse_check_skips_layouts_that_are_meant_to_breathe():
    nodes = [_text_node("n1", 100, 300, 200, 30, "谢谢")]
    for layout in ("cover", "section", "end", "quote", "table", "appendix"):
        report = lint.inspect(_measured(nodes), _tokens(), layout=layout)
        assert not any(i.rule == "sparse" for i in report.issues), layout


# ---------------------------------------------------------------- 修复阶梯


def test_repair_ladder_is_ordered_and_terminates():
    report = lint.PageReport(index=1, layout="chart")
    first = lint.next_repair(report, has_cards=True)
    assert first.action == "tighten" and first.scale < 1
    report.repairs.append(first.label())
    second = lint.next_repair(report, has_cards=True)
    assert second.action == "relayout" and second.layout == "cards"
    report.repairs.append(second.label())
    third = lint.next_repair(report, has_cards=True)
    assert third.action == "trim" and third.drop == 1
    report.repairs.append(third.label())
    assert lint.next_repair(report, has_cards=True) is None


def test_repair_falls_back_to_bullets_when_no_card_material():
    report = lint.PageReport(index=1, layout="chart", repairs=["收字阶 ×0.93"])
    assert lint.next_repair(report, has_cards=False).layout == "bullets"


def test_repair_on_simplest_layout_trims_instead_of_relayouting():
    report = lint.PageReport(index=1, layout="bullets", repairs=["收字阶 ×0.93"])
    assert lint.next_repair(report).action == "trim"


def test_summarize_counts_errors_and_repairs():
    a = lint.PageReport(index=0, layout="cover", fill=0.2)
    b = lint.PageReport(index=1, layout="chart", fill=0.3,
                        issues=[lint.Issue("text-clipped", "error", "裁切")],
                        repairs=["收字阶 ×0.93"])
    summary = lint.summarize([a, b])
    assert summary["pages"] == 2
    assert summary["clean_pages"] == 1
    assert summary["errors"] == 1
    assert summary["repairs"] == 1


# ---------------------------------------------------------------- 原生写出


def test_write_deck_emits_editable_text_not_images():
    tokens = _tokens()
    measured = [_measured([
        _shape_node("n1", 64, 200, 400, 120, "rgb(16, 25, 47)", radius=[14, 14, 14, 14]),
        _text_node("n2", 84, 220, 360, 40, "原生可编辑文字"),
    ])]
    prs = Presentation()
    prs.slide_width = pptx_writer.px(tk.CANVAS_W)
    prs.slide_height = pptx_writer.px(tk.CANVAS_H)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    counts = pptx_writer.write_slide(slide, measured[0], tokens)
    assert counts["text_blocks"] == 1
    assert counts["text_lines"] == 1
    def _walk(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from _walk(shape.shapes)
            else:
                yield shape

    texts = [s.text_frame.text for s in _walk(slide.shapes) if s.has_text_frame]
    assert "原生可编辑文字" in texts
    assert not any(s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in _walk(slide.shapes))
    # 主题样式必须剥掉，否则换母版就变样
    assert all(s._element.find(qn("p:style")) is None for s in _walk(slide.shapes))


def test_gradient_fill_is_schema_ordered_and_angle_optional():
    """a:gradFill 必须紧跟几何元素；追加到 effectLst 之后 PowerPoint 会忽略它。"""
    tokens = _tokens()
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    node = _shape_node("n1", 100, 300, 118, 260, "rgba(0, 0, 0, 0)", cls="col-bar",
                       gradient="linear-gradient(rgb(79, 107, 246) 0%, rgb(125, 145, 248) 100%)",
                       radius=[10, 10, 0, 0])
    shape = pptx_writer._add_rect(slide.shapes, node["rect"], node["style"], name="col-bar")
    assert shape is not None, "省略角度的渐变不能让整个形状消失"
    sp_pr = shape._element.spPr
    tags = [child.tag.split("}")[1] for child in sp_pr]
    assert "gradFill" in tags
    assert tags.index("gradFill") == tags.index("prstGeom") + 1
    assert tokens["chart"][0] == "4F6BF6"


def test_donut_splits_segments_crossing_zero_degrees():
    """PowerPoint 把 pie 角度钳在 0~360°，跨 0° 的扇形必须拆两段。"""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    node = {
        "id": "d1", "kind": "directive", "directive": "donut",
        "rect": {"x": 100, "y": 100, "w": 300, "h": 300},
        "style": {}, "data": {"hole": "0.6", "hole-fill": "0B1223", "segments": json.dumps([
            {"start": 300.0, "end": 420.0, "fill": "4F6BF6"},
            {"start": 60.0, "end": 120.0, "fill": "14B8C4"},
        ])},
    }
    pptx_writer._add_donut(slide.shapes, node)
    pies = [s for s in slide.shapes if s.name == "donut-segment"]
    assert len(pies) == 3  # 跨 0° 的那段拆成 2 + 正常 1
    for pie in pies:
        assert 0 <= pie.adjustments[1] <= 216.0  # 360° × 0.6


def test_write_deck_roundtrips_through_bytes():
    tokens = _tokens()
    measured = [_measured([_text_node("n1", 64, 100, 600, 50, "标题文字")], index=0)]
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        stats = pptx_writer.write_deck(measured, tokens, Path(tmp) / "d.pptx", notes=["备注"])
        assert stats["total_slides"] == 1
        prs = Presentation(io.BytesIO(Path(stats["path"]).read_bytes()))
        assert len(prs.slides.__iter__.__self__._sldIdLst) == 1
        assert prs.slides[0].notes_slide.notes_text_frame.text == "备注"

# ---------------------------------------------------------------- 大数字槽位

def test_split_stat_never_puts_prose_in_the_number_slot():
    """散文的前六个字塞进 100px+ 的数字槽会折成一根捅穿画布的柱子。"""
    prose = "设备状态从人工上报转为在线记录，班次追溯有了统一口径"
    num, unit, rest = compose._split_stat(prose)
    assert num == "" and unit == ""
    assert rest == prose
    # 句中有真实指标时取指标，说明保留整句
    assert compose._split_stat("剩余 9% 未联网设备决定 2027 年数据完整度的上限")[:2] == ("9", "%")
    # 数字打头的老写法不受影响
    assert compose._split_stat("64% 换线时间压缩") == ("64", "%", "换线时间压缩")


def test_outcome_metric_skips_year_references():
    """「剩余 9%…2027 年…」的结果是 9%，不是年份；整句只有年份就不算指标。"""
    assert compose._outcome_metric("剩余 9% 未联网设备决定 2027 年数据完整度的上限") == ("9", "%")
    assert compose._outcome_metric("2027 年要把质量动作前移到关键工序参数") is None
    assert compose._outcome_metric("良率由 93.4% 提高到 97.8%") == ("97.8", "%")


def test_fit_number_charges_full_width_for_cjk():
    """汉字占 1em，按 0.30em 估算会给出撑破卡片两倍宽的字号。"""
    cjk = compose._fit_number("设备联网", "", 300, 150)
    assert 4 * cjk <= 300
    latin = compose._fit_number("1234", "", 300, 150)
    assert 4 * 0.60 * latin <= 300
    assert cjk < latin


def test_stat_page_without_numbers_uses_ordinal_not_giant_prose():
    outline = _outline()
    outline.sections[1].pages[0].bullets = [
        "设备状态从人工上报转为在线记录", "班次追溯有了统一口径", "管理层要区分看得到和用得上",
    ]
    html = compose.compose_deck(outline, _tokens())[1].html
    assert 'class="stat-num"' not in html
    assert html.count('class="card-ghost"') == 3


def test_ghost_ordinal_stays_readable_on_every_family():
    """序号占的是大数字槽，半透明色相在深底卡上只有 1.8:1，等于没写。"""
    outline = _outline()
    outline.sections[1].pages[0].bullets = [
        "设备状态从人工上报转为在线记录", "班次追溯有了统一口径", "管理层要区分看得到和用得上",
    ]
    for family in ("midnight", "graphite", "ocean", "sunset", "forest", "ivory"):
        tokens = _tokens(family, accent="#E8A03C")
        html = compose.compose_deck(outline, tokens)[1].html
        card = tokens["colors"]["card"]
        for chunk in html.split('class="card-ghost" style="color:')[1:]:
            ink = chunk.split('"', 1)[0]
            assert color.contrast(ink, card) >= 3.0, (family, ink, card)


def test_stat_row_shares_tracks_so_numbers_and_rules_line_up():
    """各卡数字字号与说明行数都不同，各自居中会让整行散成四个高度。"""
    tokens = _tokens()
    html = compose.compose_deck(_outline(), tokens)[1].html
    assert 'class="stats" style="grid-template-columns:' in html
    assert "grid-template-rows:subgrid" in stylesheet.stylesheet()
    # 卡片宽度由 grid 轨道决定，不能再留 flex 权重跟轨道打架
    assert '<div class="stat" style="flex:' not in html


def test_cards_do_not_repeat_a_bare_metric_twice():
    """「2025 年平均换线：48 分钟」只该出现一次 48，且是大号那次。"""
    outline = _outline()
    outline.sections[1].pages[2].bullets = [
        "2025 年平均换线：48 分钟", "2026 年平均换线：19 分钟", "单次换线节省：29 分钟",
    ]
    html = compose.compose_deck(outline, _tokens())[3].html
    assert html.count("48") == 1
    assert 'class="stat-num"' in html
    assert 'class="card-metric"' not in html


def test_cards_never_guess_a_headline_number_from_prose():
    """「19 分钟…避免回到 48 分钟」的最后一个数字是旧值，放大成结论就是编结论。"""
    outline = _outline()
    outline.sections[1].pages[2].bullets = [
        "换线固化：19 分钟要写进标准作业，避免订单波动时回到 48 分钟",
        "未联网设备：91% 之后仍有 9% 空白，先定采集标准",
        "延迟订单：96% 准时率意味着 4% 延迟，按客户拆原因",
    ]
    html = compose.compose_deck(outline, _tokens())[3].html
    assert 'class="card-metric"' not in html
    assert 'class="card-ghost"' not in html


def test_empty_bullets_do_not_gut_cover_section_end_appendix():
    """结尾页本来就没有要点，回落成 bullets 只会得到一页空白。"""
    outline = _outline()
    kinds = [s.kind for s in compose.compose_deck(outline, _tokens())]
    assert kinds[0] == "cover"
    assert kinds[-2] == "end"
    assert kinds[-1] == "appendix"
    for layout in ("end", "section", "appendix"):
        assert compose._feasible(layout, outline.sections[0].pages[0], index=3, total=9) == layout


# ---- 画布比例


def test_canvas_for_keeps_exact_96dpi_for_common_sizes():
    """16:9 与 4:3 在 96dpi 下都是整数，pptx 页面才能和模板严丝合缝。"""
    assert tk.canvas_for(13.333, 7.5) == (1280, 720)
    assert tk.canvas_for(10.0, 7.5) == (960, 720)
    assert tk.canvas_for(0, 0) == (tk.CANVAS_W, tk.CANVAS_H)


def test_four_three_deck_renders_and_writes_at_its_own_ratio(tmp_path):
    preset = dict(designs.default_preset())
    preset["canvas"] = tk.canvas_for(10.0, 7.5)
    tokens = tk.resolve_tokens(preset)
    assert (tokens["canvas_w"], tokens["canvas_h"]) == (960, 720)
    assert tokens["css_vars"]["--canvas-w"] == "960px"

    slides = compose.compose_deck(_outline(), tokens)
    assert "width:var(--canvas-w)" in slides[0].html
    measured = measure.measure(slides[:2], tmp_path, canvas=(960, 720))
    assert all(m["size"]["w"] == 960 for m in measured)
    for item in measured:
        report = lint.inspect(item, tokens, layout=slides[int(item["index"])].kind)
        assert not report.errors, [i.detail for i in report.errors]

    out = tmp_path / "deck.pptx"
    pptx_writer.write_deck(measured, tokens, out)
    prs = Presentation(str(out))
    assert (prs.slide_width, prs.slide_height) == (9144000, 6858000)


def test_card_row_shares_one_top_edge(tmp_path):
    """卡片行按内容定高、整组居中。

    拉满版心 + 每张卡各自居中，会让序号与标题在行内高低不齐（正文行数不同），
    底部还空出一大片——三张卡看起来像三个独立的块，不是一组。
    """
    for canvas in ((tk.CANVAS_W, tk.CANVAS_H), (960, 720)):
        preset = dict(designs.default_preset())
        preset["canvas"] = canvas
        tokens = tk.resolve_tokens(preset)
        slides = compose.compose_deck(_outline(), tokens)
        index = next(i for i, s in enumerate(slides) if s.kind == "cards")
        item = measure.measure([slides[index]], tmp_path / f"{canvas[0]}", canvas=canvas)[0]
        chips = [n for n in item["nodes"]
                 if n.get("kind") == "text" and "chip" in str(n.get("className") or "").split()]
        heads = [n for n in item["nodes"]
                 if n.get("kind") == "text" and "h3" in str(n.get("className") or "").split()]
        assert len(chips) == 3 and len(heads) == 3
        assert len({round(c["rect"]["y"]) for c in chips}) == 1, canvas
        assert len({round(h["rect"]["y"]) for h in heads}) == 1, canvas
        report = lint.inspect(item, tokens, layout="cards")
        assert not report.errors, [i.detail for i in report.errors]


def test_prose_fitter_leaves_layouts_that_already_fit_untouched():
    """收字号只在真放不下时发生，且两条要点流各按各的字阶起步。

    裸列表走 t-body、实底横幅走 t-lead，横幅还没有上下内边距。基准档要是取错了，
    「本来就放得下」的判断就会失真，把没坏的版式一起改了。
    """
    tokens = _tokens()
    short = ["统一 17 套系统", "回滚耗时 3 分钟", "沉淀 240 条规则"]
    assert compose._prose_fit(short, 700, 400, tokens) == (tokens["types"]["body"], 16)
    assert compose._prose_fit(short, 700, 400, tokens, band=True) == (tokens["types"]["lead"], 0)


_LONG_PROSE = [
    "成本侧：单位制造成本下降 12%，是五项指标里唯一直接改善损益表的结果",
    "成本侧：改善来自换线损失与不良损失两条线，靠效率挤出来，不是靠砍维护和培训投入",
    "交付侧：交付准时率 88%→96%，晚交订单占比从 12% 降到 4%",
    "交付侧：换线时间缩短后，小批量插单不再挤占主线排程，准时率提升不是靠压库存换来的",
    "两侧同向：效率改善最终同时体现在成本和交付上，没有互相拆台",
]


def test_long_prose_neither_overlaps_nor_drops_content(tmp_path):
    """深化后的要点是 30~45 字的整句，不是标签。

    .b-item 原来是 flex:1 1 0，条目被压得比正文还矮，文字不裁切、直接压住下一条
    （体检报「文字重叠」）；卡片页则是每张卡折七八行顶穿画布。两条路都得靠收字号
    或换构图解决，一个字都不能删——减要点是修复阶梯的最后一步，不该是默认结果。
    """
    for layout in ("two_col", "cards"):
        for canvas in ((tk.CANVAS_W, tk.CANVAS_H), (960, 720)):
            preset = dict(designs.default_preset())
            preset["canvas"] = canvas
            tokens = tk.resolve_tokens(preset)
            outline = _outline()
            page = outline.sections[1].pages[2]
            page.layout = layout
            page.bullets = list(_LONG_PROSE)
            slides = compose.compose_deck(outline, tokens)
            index = next(i for i, s in enumerate(slides) if s.kind == layout)
            item = measure.measure(
                [slides[index]], tmp_path / f"{layout}-{canvas[0]}", canvas=canvas
            )[0]
            report = lint.inspect(item, tokens, layout=layout)
            assert not report.errors, (layout, canvas, [i.detail for i in report.errors])
            rendered = "".join(
                "".join(run["text"] for line in node["lines"] for run in line["runs"])
                for node in item["nodes"] if node.get("kind") == "text"
            ).replace(" ", "")
            for bullet in _LONG_PROSE:
                # 卡片版会把「小标题：正文」拆成标题与正文两块，冒号是分隔符不是内容
                for part in bullet.split("：", 1):
                    assert part.replace(" ", "") in rendered, (layout, canvas, part)


def test_cover_stat_row_is_equal_width_and_shares_baselines(tmp_path):
    """封面数字条：等宽三栏、数字与说明各自对齐、卡内不留幽灵行。

    .stat 本来靠 .stats 的 5 轨 subgrid 定位，封面却把它放进 flex 行里：subgrid
    失效后 nth-child(1) 的 grid-row:2 空出一条 16px 幽灵行，而内容定宽让长标签那张卡
    宽出一倍多，整行右边空一大片。装饰色条也不能贴着卡片下沿——看着像卡片漏出来的下划线。
    """
    for canvas in ((tk.CANVAS_W, tk.CANVAS_H), (960, 720)):
        preset = dict(designs.default_preset())
        preset["canvas"] = canvas
        tokens = tk.resolve_tokens(preset)
        slides = compose.compose_deck(_outline(), tokens)
        item = measure.measure(slides[:1], tmp_path / f"{canvas[0]}", canvas=canvas)[0]
        cards = [n for n in item["nodes"] if "stat" in str(n.get("className") or "").split()]
        assert len(cards) == 3, item["nodes"]
        assert len({round(c["rect"]["w"]) for c in cards}) == 1, canvas
        # 整行铺满版心，而不是挤在左边
        span = max(c["rect"]["x"] + c["rect"]["w"] for c in cards) - min(c["rect"]["x"] for c in cards)
        assert span >= canvas[0] - tokens["margin"] * 2 - 1, (canvas, span)

        nums = [n for n in item["nodes"] if n.get("className") == "stat-num"]
        labels = [n for n in item["nodes"] if n.get("className") == "stat-label"]
        assert len(nums) == 3 and len(labels) == 3
        assert len({round(n["rect"]["y"]) for n in nums}) == 1, canvas
        assert len({round(n["rect"]["y"]) for n in labels}) == 1, canvas
        # 卡顶到数字就是内边距，没有多出来的空轨道
        pad = round(nums[0]["rect"]["y"] - cards[0]["rect"]["y"])
        assert pad <= tokens["pad"] + 1, (canvas, pad, tokens["pad"])
        # 说明是短标签：折行也要折得均匀，不许甩出一个字的孤行
        for label in labels:
            lines = ["".join(r["text"] for r in ln["runs"]) for ln in label["lines"]]
            if len(lines) > 1:
                assert len(lines[-1].strip()) > 1, (canvas, lines)

        bar = next(n for n in item["nodes"] if n.get("className") == "m-bar")
        bottom = max(c["rect"]["y"] + c["rect"]["h"] for c in cards)
        assert bar["rect"]["y"] - bottom >= 24, (canvas, bar["rect"], bottom)


def test_cover_title_shrinks_to_avoid_an_orphan_line_on_narrow_canvas(tmp_path):
    """4:3 画布窄 25%，84px 的十字标题会挤出一个字的孤行（「…经营复」/「盘」）。

    字阶是物理磅值不能整体缩放，所以只让大标题按可用宽度收一点。
    """
    title = "智能制造年度经营复盘"
    preset = dict(designs.default_preset())
    preset["canvas"] = tk.canvas_for(10.0, 7.5)
    tokens = tk.resolve_tokens(preset)
    wide = tk.resolve_tokens(designs.default_preset())
    assert compose._display_px(title, wide, 1136) == wide["types"]["cover"]
    assert compose._display_px(title, tokens, 796) < tokens["types"]["cover"]
    # 再长也不能缩成小字：到下限就正常折行
    assert compose._display_px("长" * 40, tokens, 796) == int(tokens["types"]["cover"] * 0.6)

    outline = _outline()
    outline.title = title
    outline.sections[0].pages[0].title = title
    slides = compose.compose_deck(outline, tokens)
    measured = measure.measure(slides[:1], tmp_path, canvas=(960, 720))
    node = next(n for n in measured[0]["nodes"] if n.get("className") == "cover-title")
    assert len(node["lines"]) == 1, [ln["runs"][0]["text"] for ln in node["lines"]]
    assert node["rect"]["w"] <= 960 - tokens["margin"] * 2 + 1


def test_timeline_text_flows_inline_around_highlighted_numbers(tmp_path):
    """时间轴正文里的数字是行内高亮，不是并排的一栏。

    .tl-text 原来是 display:flex，highlight() 包出来的 <span> 就成了 flex item：
    句子被拆成「前半句 | 数字 | 后半句」三列各自折行，中间还留一个洞
    （「…压到日均」/「80 条」/「以内」）。垂直居中该由 .tl-band 负责。
    """
    bullets = [
        "Q1：统一设备主数据，12 条产线全部接入唯一编号体系",
        "Q2：告警分级上线，把需要人工处置的提示压到日均 80 条以内",
        "Q3：实时良率看板覆盖四个基地，异常响应目标 15 分钟",
    ]
    for canvas in ((tk.CANVAS_W, tk.CANVAS_H), (960, 720)):
        preset = dict(designs.default_preset())
        preset["canvas"] = canvas
        tokens = tk.resolve_tokens(preset)
        outline = _outline()
        page = outline.sections[1].pages[2]
        page.layout = "timeline"
        page.bullets = list(bullets)
        slides = compose.compose_deck(outline, tokens)
        index = next(i for i, s in enumerate(slides) if s.kind == "timeline")
        item = measure.measure([slides[index]], tmp_path / f"{canvas[0]}", canvas=canvas)[0]
        texts = [n for n in item["nodes"] if n.get("className") == "tl-text"]
        assert len(texts) == len(bullets), [n.get("className") for n in item["nodes"]]
        for node, bullet in zip(texts, bullets):
            lines = node["lines"]
            # 块级流每行左边缘对齐；flex 碎片会各自从不同的 x 起头
            assert len({round(ln["x"]) for ln in lines}) == 1, [ln["x"] for ln in lines]
            joined = "".join("".join(r["text"] for r in ln["runs"]) for ln in lines)
            assert joined.replace(" ", "") == bullet.split("：", 1)[1].replace(" ", "")
        report = lint.inspect(item, tokens, layout="timeline")
        assert not report.errors, (canvas, [i.detail for i in report.errors])


def test_content_title_shrinks_rather_than_stranding_one_character(tmp_path):
    """内页标题也要躲孤字行。

    4:3 上「2027 年先补三块短板：联网、换线、良率」会把「率」单独甩到第二行，
    看着像排版事故，还白吃掉 60px 正文高度——正文被迫降版式就是被它挤的。
    收字号只在一行装得下、且代价不超过两成时才做，收不动就照常折行。
    """
    title = "2027 年先补三块短板：联网、换线、良率"
    preset = dict(designs.default_preset())
    preset["canvas"] = (960, 720)
    tokens = tk.resolve_tokens(preset)
    base = tokens["types"]["h1"]
    assert int(base * 0.78) <= compose._title_px(title, tokens) < base
    # 本来就一行装得下的标题一个字都不动
    assert compose._title_px("关键指标", tokens) == base
    # 长到收不动就保持原字号，不该为了凑单行缩成小标题
    assert compose._title_px("长" * 40, tokens) == base

    outline = _outline()
    outline.sections[1].pages[2].title = title
    slides = compose.compose_deck(outline, tokens)
    index = next(i for i, s in enumerate(slides) if s.kind == "cards")
    item = measure.measure([slides[index]], tmp_path, canvas=(960, 720))[0]
    node = next(n for n in item["nodes"] if n.get("className") == "h1")
    assert len(node["lines"]) == 1, [ln["runs"][0]["text"] for ln in node["lines"]]


def test_cover_subtitle_breaks_at_the_colon_not_mid_word():
    """封面副题在冒号后断行。

    中文没有词边界，交给浏览器折行会从词中间劈开（「…良率、成 / 本、交付」）；
    text-wrap:balance 只是把两行裁得一样长，劈开的位置照样随机。冒号两边本来就是
    两层意思，断在语义上才像人排的。
    """
    long = "五项年度指标全部达成：联网、换线、良率、成本、交付"
    assert compose._cover_sub(long) == "五项年度指标全部达成：<br>联网、换线、良率、成本、交付"
    assert compose._cover_sub("短副题") == "短副题"
    assert "<br>" not in compose._cover_sub("没有冒号但确实很长很长很长很长很长的一段副标题")


def test_light_theme_hues_and_cover_text_clear_contrast_floor():
    """浅色主题：图表色与封面次要文字都要过对比度线。

    这两个坑都实际踩过：
    - 大数字色沿着「向白混合」找可读色——深色底上对，浅色底上越混越看不见
      （白底橙字 2.5:1），必须按 mode 换方向；
    - 封面底色是渐变，--c-muted 按白底挑的，落到最深色标只有 3.3:1。
    检测器把两处都报了出来，这里把修法钉住。
    """
    tokens = _tokens()
    tokens["mode"] = "light"
    tokens["colors"] = {
        **tokens["colors"],
        "bg": "FFFFFF", "card": "FFFFFF", "ink": "171E26",
        "muted": "606E80", "accent_ink": "195DB6",
    }
    tokens["cover_stops"] = ["FFFFFF", "F2F6FD", "D9E7F3", "B7D1F3"]
    worst = min(tokens["cover_stops"], key=lambda s: color.contrast(s, tokens["colors"]["ink"]))

    for index in range(len(tokens["chart"])):
        hue = compose._readable_hue("FFFFFF", tk.chart_hue(tokens, index), tokens)
        assert color.contrast(hue, "FFFFFF") >= 4.5, (index, hue)

    css = compose._cover_text_vars(tokens)
    muted = css.split("--cover-muted:")[1].split(";")[0]
    accent = css.split("--cover-accent-ink:")[1].split(";")[0]
    assert color.contrast(muted, worst) >= 4.5, (muted, worst)
    assert color.contrast(accent, worst) >= 4.5, (accent, worst)


def test_metric_layout_turns_a_to_b_into_a_track(tmp_path):
    """「62%→91%」这类页面要画成达成轨道，而不是继续摆三张等权大数字卡。

    一张稿子里连着四页「三个大数字」是上一版最单调的地方；轨道至少让读者
    一眼看出从哪来、到哪去、差多少，而且数字全部来自原文，不编。
    """
    bullets = [
        "设备联网率 62%→91%：一年提升 29 个百分点，跨过 90% 这条可用线",
        "每 10 台设备有 9 台在线，停机原因不再靠班组长手写台账事后追认",
        "联网后单台设备的停机时长可按班次拆解，追溯从「翻记录」变成「查数据」",
    ]
    for canvas in ((tk.CANVAS_W, tk.CANVAS_H), (960, 720)):
        preset = dict(designs.default_preset())
        preset["canvas"] = canvas
        tokens = tk.resolve_tokens(preset)
        outline = _outline()
        page = outline.sections[1].pages[2]
        page.layout = "auto"
        page.title = "设备联网率 62%→91%，数据采集跨过可用线"
        page.bullets = list(bullets)
        slides = compose.compose_deck(outline, tokens)
        index = next(i for i, s in enumerate(slides) if s.kind == "metric")
        item = measure.measure([slides[index]], tmp_path / f"m-{canvas[0]}", canvas=canvas)[0]
        report = lint.inspect(item, tokens, layout="metric")
        assert not report.errors, (canvas, [i.detail for i in report.errors])
        # 大数字、起点、增量都在版面上，且说明文字一条不少
        texts = "".join(
            "".join(run["text"] for line in node["lines"] for run in line["runs"])
            for node in item["nodes"] if node.get("kind") == "text"
        ).replace(" ", "")
        for token in ("91", "62%", "+29个百分点"):
            assert token in texts, (canvas, token)
        assert "停机时长可按班次拆解" in texts


def test_metric_run_alternates_between_overview_and_spotlight():
    """一串指标页不要三连同款：首张留总览（大数字），后面换成达成轨道。"""
    tokens = _tokens()
    # 首页是三指标总览（三个 A→B），后两页是单指标深挖
    pages = [{
        "title": "五项指标全部达成",
        "layout": "auto",
        "bullets": [
            "联网率 62% 到 91%，提升 29 个百分点",
            "换线 48 到 19 分钟，降幅 60%",
            "良率 93.4% 到 97.8%，首次站上 97%",
        ],
    }]
    for before, after in ((48, 19), (93.4, 97.8)):
        pages.append({
            "title": f"该项从 {before} 到 {after}",
            "layout": "auto",
            "bullets": [
                f"该项从 {before} 到 {after}，变化幅度明显",
                "成因来自流程改造，而不是压缩维护投入",
                "结果已经体现在单位制造成本上",
            ],
        })
    outline = Outline.model_validate({
        "title": "指标复盘",
        "style": {"theme_pack_id": "builtin:midnight"},
        "sections": [
            {"title": "开场", "pages": [{"title": "指标复盘", "layout": "cover"}]},
            {"title": "指标", "pages": pages},
            {"title": "收尾", "pages": [{"title": "谢谢", "layout": "end"}]},
        ],
    }).normalize()
    slides = compose.compose_deck(outline, tokens)
    kinds = [s.kind for s in slides]
    assert kinds.count("metric") == 3, kinds
    # 首页是仪表盘（一行一项），后两页是单指标聚焦（大数字 + 轨道）
    assert "metrics--lines" in slides[1].html, "首页应为仪表盘"
    assert any("metric-spot" in slide.html for slide in slides), "深挖页应为聚焦版式"


def test_dashboard_puts_every_metric_on_one_page():
    """3 项以上指标 → 仪表盘：一行一条轨道，而不是三张等权大数字卡。

    大数字卡还有个更隐蔽的问题：`_outcome_metric` 取句尾数字，
    「联网率 62%→91%，提升 29 个百分点」会显示成 29，读者以为那就是联网率。
    """
    bullets = [
        "设备联网率 62%→91%：一年提升 29 个百分点",
        "换线时间 48→19 分钟：降幅 60%",
        "一次良率 93.4%→97.8%：不良只剩三分之一",
    ]
    tokens = _tokens()
    outline = _outline()
    page = outline.sections[1].pages[2]
    page.layout = "auto"
    page.title = "五项指标全部达成"
    page.bullets = list(bullets)
    slides = compose.compose_deck(outline, tokens)
    index = next(i for i, s in enumerate(slides) if s.kind == "metric")
    assert "metrics--lines" in slides[index].html
    assert "91" in slides[index].html and "29 个百分点" in slides[index].html
    # 仪表盘只用一种强调色：一页里五行不同颜色会让人以为颜色有含义
    hues = set(re.findall(r"--hue:(#[0-9A-Fa-f]{6})", slides[index].html))
    assert len(hues) <= 1, hues


def test_inline_series_makes_a_real_chart_from_prose():
    """序列写在一句话里也要能成图，而且只用原文的数字。"""
    assert compose._inline_series(["1 月 88.2%、2 月 89.1%、3 月 90.6%、4 月 92.3%"]) == [
        ("1 月", 88.2, "%"), ("2 月", 89.1, "%"), ("3 月", 90.6, "%"), ("4 月", 92.3, "%"),
    ]
    ranged = compose._inline_series(["1—4 月良率 88.2%、89.1%、90.6%、92.3%"])
    assert [value for _, value, _ in ranged] == [88.2, 89.1, 90.6, 92.3]
    assert compose._inline_series(["覆盖率 62%→91%，只有两个点"]) is None
    assert compose._inline_series(["没有数字的一句话"]) is None


def test_narrow_trend_uses_a_line_and_wide_one_stays_columns():
    """88→92 的柱子几乎等高，必须换折线；20→90 的柱状才有对照意义。"""
    tokens = _tokens()
    narrow = [("1 月", 88.2, "%"), ("2 月", 89.1, "%"), ("3 月", 90.6, "%"), ("4 月", 92.3, "%")]
    wide = [("1 月", 20.0, "%"), ("2 月", 48.0, "%"), ("3 月", 71.0, "%"), ("4 月", 90.0, "%")]
    narrow_html = compose._chart(_page_with(narrow), _outline(), tokens, "", 0, 0)
    wide_html = compose._chart(_page_with(wide), _outline(), tokens, "", 0, 0)
    assert 'data-pptx="spark"' in narrow_html
    assert 'data-pptx="spark"' not in wide_html


def test_spark_directive_becomes_native_connectors():
    """折线在 pptx 里是普通连接符，每个顶点都能单独拖，不依赖图表数据源。"""
    from pptx import Presentation
    from app.services.ppt_studio.htmlkit import pptx_writer

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    node = {
        "kind": "directive", "directive": "spark",
        "rect": {"x": 100, "y": 100, "w": 600, "h": 300},
        "style": {}, "group": "spark",
        "data": {"points": "[[0,300],[200,220],[400,140],[600,60]]",
                 "vw": "600", "vh": "300", "color": "4F6BF6", "stroke": "3", "dots": "0"},
    }
    counts = pptx_writer.write_slide(slide, {"index": 0, "nodes": [node]}, _tokens())
    assert counts["directives"] == 1
    connectors = [shape for shape in slide.shapes if shape.shape_type == 20 or "Connector" in type(shape).__name__]
    assert len(connectors) >= 3


def _page_with(pairs):
    from app.services.ppt_studio.outline import OutlinePage

    return OutlinePage.model_validate({
        "title": "趋势",
        "layout": "chart",
        "bullets": [" ".join(f"{label} {value:g}{unit}" for label, value, unit in pairs)],
    })


def test_stat_without_a_unit_does_not_crash():
    """「交付准时率 96」这种没有单位的数字，也要能排成大数字卡。

    单位的正则分组是可选的，`group(2)` 会返回 None；`_fit_number` 里直接遍历
    单位字符串会抛 TypeError——线上就是在这里 500 的。
    """
    assert compose._split_stat("交付准时率 96") == ("96", "", "交付准时率")
    assert compose._split_stat("3200 台设备联网") == ("3200", "台", "设备联网")
    assert compose._fit_number("96", None, 300, 120) > 0

    tokens = _tokens()
    outline = _outline()
    page = outline.sections[1].pages[2]
    page.layout = "stat"
    page.bullets = ["交付准时率 96", "换线 19", "良率 97.8"]
    slides = compose.compose_deck(outline, tokens)
    assert any("stat-num" in slide.html for slide in slides)


def test_appendix_paginates_clear_of_the_footer(tmp_path):
    """来源多的时候分页，且最后一条不许压在页码上。"""
    tokens = _tokens()
    outline = _outline()
    model = type(outline.sources[0])
    outline.sources = [
        model.model_validate({"id": f"s{i}", "kind": "web",
                              "title": f"来源资料第 {i} 号", "url": f"https://example.com/{i}"})
        for i in range(1, 13)
    ]
    for page in outline.flatten_pages():
        page.sources = list(outline.sources)
    slides = compose.compose_deck(outline, tokens)
    appendix = [slide for slide in slides if slide.kind == "appendix"]
    assert len(appendix) >= 2, len(appendix)
    items = measure.measure(appendix, tmp_path, canvas=(tk.CANVAS_W, tk.CANVAS_H))
    for item in items:
        report = lint.inspect(item, tokens, layout="appendix")
        assert not report.errors, [issue.detail for issue in report.errors]
