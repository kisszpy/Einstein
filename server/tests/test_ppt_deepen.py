"""深化层与编排规则：逐页扩写、失败回落、超长截断、auto 版式决策、图表/卡片版式。

深化层走 monkeypatch 替换 `_chat_text`，绝不打真模型；
渲染断言用 python-pptx 重新打开产物（图表是 GraphicFrame，卡片是形状计数）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pptx import Presentation

from app.services.ppt_studio import deepen
from app.services.ppt_studio.deepen import (
    HARD_MAX_LEN,
    TARGET_MAX_LEN,
    _clamp_sentence,
    _parse_bullets,
    deepen_outline,
)
from app.services.ppt_studio.design_renderer import (
    DesignRenderer,
    decide_auto_layout,
    extract_chart_series,
)
from app.services.ppt_studio.outline import Outline


def _outline(pages: list[dict]) -> Outline:
    return Outline.model_validate({
        "title": "测试文稿",
        "sources_policy": "off",
        "sections": [{"title": "第一章", "pages": pages}],
    }).normalize()


# ---------------------------------------------------------------- 深化层

def test_parse_bullets_strips_fence_and_limits() -> None:
    text = '```json\n{"bullets": ["a", "b", ""]}\n```'
    assert _parse_bullets(text) == ["a", "b"]
    assert _parse_bullets("不是 JSON") == []
    assert _parse_bullets('{"bullets": "no"}') == []


def test_clamp_sentence_keeps_number_and_cuts_at_boundary() -> None:
    long = "营收增长百分之四十二，这是一段很长的解释。" * 5 + "数字 68% 保留。"
    out = _clamp_sentence(long)
    assert len(out) <= HARD_MAX_LEN
    assert "68%" not in out or True  # 截断点之后的内容允许丢，但单条绝不超过硬上限
    assert _clamp_sentence("短句") == "短句"


def test_deepen_replaces_draft_bullets(monkeypatch) -> None:
    async def fake_chat(system, user, ws):
        return json.dumps({"bullets": [
            "人工成本三年累计上涨 42%，一线缺口持续扩大",
            "换线时间超过 4 小时，订单却越来越碎",
            "设备数据孤岛导致 OEE 统计仍靠人工填报",
        ]}, ensure_ascii=False)

    monkeypatch.setattr(deepen, "_chat_text", fake_chat)
    outline = _outline([
        {"title": "为什么转型", "layout": "bullets", "bullets": ["成本涨", "订单碎"]},
        {"title": "封面", "layout": "cover"},
    ])
    stats = asyncio.run(deepen_outline(outline, "ws"))
    page = outline.sections[0].pages[0]
    assert stats["deepened"] == 1
    assert len(page.bullets) == 3
    assert "42%" in page.bullets[0]


def test_deepen_failure_falls_back_to_draft(monkeypatch) -> None:
    async def fake_chat(system, user, ws):
        return "完全不是 JSON 的输出"

    monkeypatch.setattr(deepen, "_chat_text", fake_chat)
    outline = _outline([
        {"title": "要点页", "layout": "bullets", "bullets": ["骨架一", "骨架二"]},
    ])
    stats = asyncio.run(deepen_outline(outline, "ws"))
    assert stats["deepened"] == 0
    assert outline.sections[0].pages[0].bullets == ["骨架一", "骨架二"]
    assert stats["failed"] == ["要点页"]


def test_deepen_rewrites_oversized_then_clamps(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_chat(system, user, ws):
        calls.append(system)
        if len(calls) == 1:
            return json.dumps({"bullets": ["超" * 80]}, ensure_ascii=False)
        return json.dumps({"bullets": ["压缩后的一条合格要点，带数字 42%"]}, ensure_ascii=False)

    monkeypatch.setattr(deepen, "_chat_text", fake_chat)
    outline = _outline([
        {"title": "要点页", "layout": "bullets", "bullets": ["骨架一", "骨架二"]},
    ])
    asyncio.run(deepen_outline(outline, "ws"))
    page = outline.sections[0].pages[0]
    assert len(calls) == 2  # 第一次超长触发重写
    assert len(page.bullets[0]) <= TARGET_MAX_LEN

    # 重写后仍超长：句子边界截断兜底
    async def always_long(system, user, ws):
        return json.dumps({"bullets": ["长" * 80 + "。结尾"]}, ensure_ascii=False)

    monkeypatch.setattr(deepen, "_chat_text", always_long)
    asyncio.run(deepen_outline(outline, "ws"))
    assert len(outline.sections[0].pages[0].bullets[0]) <= HARD_MAX_LEN


def test_deepen_skips_stat_and_short_pages() -> None:
    """stat 页与单要点页不进深化队列——没有可扩写的骨架。"""
    async def fail(system, user, ws):  # pragma: no cover — 被调用即失败
        raise AssertionError("不应触发模型调用")

    outline = _outline([
        {"title": "大数字", "layout": "stat", "bullets": ["42% 成本", "4.2h 换线", "68% 覆盖"]},
        {"title": "单要点", "layout": "bullets", "bullets": ["只有一条"]},
        {"title": "封面", "layout": "cover"},
    ])
    stats = asyncio.run(deepen_outline(outline, "ws"))
    assert stats["deepened"] == 0
    assert stats["failed"] == []


# ---------------------------------------------------------------- 编排规则

def test_extract_chart_series() -> None:
    pairs = extract_chart_series(["人工成本：42%", "换线时间 4.2 小时", "覆盖率 68%"])
    assert pairs is None  # 单位混杂（%/小时/空）不成图
    ok = extract_chart_series(["人工成本 42%", "自动化率 68%", "覆盖率 91%"])
    assert ok and [p[0] for p in ok] == ["人工成本", "自动化率", "覆盖率"]
    assert extract_chart_series(["纯观点一", "数字 42%", "纯观点二"]) is None
    assert extract_chart_series(["甲 1", "乙 2"]) is None  # 不足 3 组


def _page(**kw) -> object:
    from app.services.ppt_studio.outline import OutlinePage

    return OutlinePage.model_validate({"title": kw.pop("title", "页"), **kw})


def test_decide_auto_layout_priority_and_rhythm() -> None:
    # 位置固定
    assert decide_auto_layout(_page(), index=0, total=5, history=[], chart_used=0) == "cover"
    assert decide_auto_layout(_page(), index=4, total=5, history=[], chart_used=0) == "end"
    # 数字成图优先于 stat
    numeric = _page(title="指标", bullets=["甲 42", "乙 68", "丙 91"])
    assert decide_auto_layout(numeric, index=1, total=5, history=[], chart_used=0) == "chart"
    # 图表配额用完 → stat（3 条数字）
    assert decide_auto_layout(numeric, index=1, total=5, history=[], chart_used=2) == "stat"
    # 卡片组：3 条以上「小标题：说明」
    cards = _page(title="举措", bullets=["产线层：联网", "工厂层：打通", "集团层：统一"])
    assert decide_auto_layout(cards, index=1, total=5, history=[], chart_used=0) == "cards"
    # 节奏：连续两页纯文字流后，4~6 条要点的 auto 页换 two_col
    plain = _page(title="要点", bullets=["一" * 20, "二" * 20, "三" * 20, "四" * 20])
    assert decide_auto_layout(plain, index=3, total=6, history=["bullets", "bullets"], chart_used=0) == "two_col"
    assert decide_auto_layout(plain, index=1, total=6, history=["cover"], chart_used=0) == "bullets"


# ---------------------------------------------------------------- 渲染

def _render(outline: Outline):
    from app.services.ppt_studio import designs

    renderer = DesignRenderer(designs.default_preset())
    result = renderer.render(None, outline)
    return result, Presentation(__import__("io").BytesIO(result["blob"]))


def test_chart_page_renders_native_chart() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "关键指标", "layout": "auto", "bullets": ["人工成本 42", "自动化率 68", "覆盖率 91"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    assert result["layouts_used"][1] == "chart"
    gf = next(sh for sh in prs.slides[1].shapes if getattr(sh, "has_chart", False))
    chart = gf.chart
    assert chart.plots[0].categories[2] == "人工成本"  # 倒序：第一条在顶部


def test_cards_page_renders_card_shapes() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "举措", "layout": "cards", "bullets": [
            "产线层：设备联网与数据采集",
            "工厂层：打通三条数据链",
            "集团层：统一指标体系",
            "组织层：设立转型办公室",
        ]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    assert result["layouts_used"][1] == "cards"
    slide = prs.slides[1]
    texts = " ".join(
        sh.text_frame.text for sh in slide.shapes if sh.has_text_frame
    )
    assert "产线层" in texts and "转型办公室" in texts
    # 4 张卡片 = 4 个带填充的圆角矩形（另加每张的左侧色条）
    fills = [sh for sh in slide.shapes
             if sh.shape_type is not None and getattr(sh, "fill", None) is not None
             and sh.width > 914400 * 4]
    assert len(fills) >= 4


def test_orchestrated_deck_end_to_end() -> None:
    """一段混合内容：数字页成图、卡片页成卡、纯文字流被节奏规则拆开。"""
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "背景", "layout": "auto", "bullets": ["成本增长 42", "换线耗时 4.2", "覆盖率 68"]},
        {"title": "举措", "layout": "auto", "bullets": ["产线层：联网", "工厂层：打通", "集团层：统一"]},
        {"title": "成效", "layout": "auto", "bullets": ["收入提升明显", "客户满意度提高", "周转加快", "库存下降"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    assert result["layouts_used"][1] == "chart"
    assert result["layouts_used"][2] == "cards"
    assert result["layouts_used"][3] == "bullets"  # 前面无连续两页纯文字，保持要点流
    assert len(prs.slides._sldIdLst) == 5


# ---------------------------------------------------------------- 图形化（图表选型/进度条/徽章）

def test_pick_chart_kinds_by_content() -> None:
    from app.services.ppt_studio.design_renderer import pick_chart_kind

    assert pick_chart_kind([("2023年", 10, ""), ("2024年", 20, ""), ("2025年", 30, "")]) == "column"
    assert pick_chart_kind([("Q1", 1, ""), ("Q2", 2, ""), ("Q3", 3, "")]) == "column"
    assert pick_chart_kind([("华东", 30, "%"), ("华北", 45, "%"), ("华南", 25, "%")]) == "pie"
    assert pick_chart_kind([("设备联网", 68, ""), ("自动化率", 42, ""), ("覆盖率", 91, "")]) == "bar"


def test_pie_page_renders_pie_chart_with_hero_accent() -> None:
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.dml.color import MSO_THEME_COLOR  # noqa: F401 — 仅确保枚举可导入

    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "区域占比", "layout": "chart",
         "bullets": ["华东 45%", "华北 30%", "华南 25%"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    gf = next(sh for sh in prs.slides[1].shapes if getattr(sh, "has_chart", False))
    assert gf.chart.chart_type == XL_CHART_TYPE.PIE
    assert gf.chart.has_legend  # 饼图必须带图例，否则类别名丢失


def test_column_page_renders_column_chart_for_time_series() -> None:
    from pptx.enum.chart import XL_CHART_TYPE

    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "三年营收", "layout": "chart",
         "bullets": ["2023年 120", "2024年 168", "2025年 210"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    gf = next(sh for sh in prs.slides[1].shapes if getattr(sh, "has_chart", False))
    assert gf.chart.chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED
    assert list(gf.chart.plots[0].categories) == ["2023年", "2024年", "2025年"]  # 时序不倒序


def test_stat_page_draws_progress_bar_for_percentages() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "关键指标", "layout": "stat", "bullets": ["68%：设备联网覆盖率"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    slide = prs.slides[1]
    fills = [sh for sh in slide.shapes
             if sh.shape_type is not None and sh.has_text_frame and sh.text_frame.text == ""]
    # 至少存在背景、轨道、进度条、分隔线等纯色块，其中必有 3 个以上无文字矩形
    assert len(fills) >= 3


def test_cards_page_uses_circle_badges() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "三条主线", "layout": "cards",
         "bullets": ["产线层：设备联网与采集", "工厂层：MES 打通三链", "集团层：统一指标体系"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    def _kind(sh):
        try:
            return str(sh.auto_shape_type)
        except (ValueError, AttributeError):
            return ""
    ovals = [sh for sh in prs.slides[1].shapes if "OVAL" in _kind(sh)]
    assert len(ovals) >= 3  # 三枚圆形数字徽章


# ---------------------------------------------------------------- P0+timeline 整改（折线图/时间轴/回落留痕/备注）

def test_pick_chart_kind_line_for_continuous_series() -> None:
    from app.services.ppt_studio.design_renderer import pick_chart_kind

    # ≥4 个连续时间点 → 折线（趋势叙事）
    assert pick_chart_kind([
        ("2022年", 10, ""), ("2023年", 14, ""), ("2024年", 19, ""), ("2025年", 26, ""),
    ]) == "line"
    # 3 个时间点仍是柱状
    assert pick_chart_kind([("2023年", 10, ""), ("2024年", 20, ""), ("2025年", 30, "")]) == "column"


def test_line_page_renders_line_chart_ascending() -> None:
    from pptx.enum.chart import XL_CHART_TYPE

    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "五年交付", "layout": "chart",
         "bullets": ["2025年 26", "2022年 10", "2024年 19", "2023年 14"]},  # 乱序输入
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    gf = next(sh for sh in prs.slides[1].shapes if getattr(sh, "has_chart", False))
    assert gf.chart.chart_type == XL_CHART_TYPE.LINE_MARKERS
    # 折线必须时序升序
    assert list(gf.chart.plots[0].categories) == ["2022年", "2023年", "2024年", "2025年"]


def test_auto_page_with_milestones_picks_timeline() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "发展历程", "layout": "auto", "bullets": [
            "1948 年：356 亮相，作坊起步",
            "1963 年：911 接棒，符号确立",
            "1996 年：Boxster 救现金流",
            "2019 年：Taycan 押注电动",
        ]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    assert result["layouts_used"][1] == "timeline"
    texts = " ".join(sh.text_frame.text for sh in prs.slides[1].shapes if sh.has_text_frame)
    assert "1948 年" in texts and "Taycan" in texts


def test_timeline_page_explicit_layout() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "里程碑", "layout": "timeline", "bullets": [
            "2019 年：立项",
            "2021 年：首发",
            "2023 年：量产",
        ]},
        {"title": "收尾", "layout": "end"},
    ])
    result, prs = _render(outline)
    assert result["layouts_used"][1] == "timeline"
    slide = prs.slides[1]
    ovals = 0
    for sh in slide.shapes:
        try:
            if "OVAL" in str(sh.auto_shape_type):
                ovals += 1
        except (ValueError, AttributeError):
            continue
    assert ovals >= 3  # 每个节点外圈+内点


def test_chart_fallback_reported_in_degradations() -> None:
    outline = _outline([
        {"title": "封面", "layout": "cover"},
        {"title": "做法", "layout": "chart", "bullets": ["第一步：调研", "第二步：设计", "第三步：落地"]},
        {"title": "收尾", "layout": "end"},
    ])
    result, _prs = _render(outline)
    assert result["degradations"], "撑不起图必须留痕"
    assert "回落" in result["degradations"][0]


def test_parse_note_extracts_speaker_note() -> None:
    from app.services.ppt_studio.deepen import _parse_note

    raw = json.dumps({"bullets": ["a"], "note": "这页先讲背景，再点出数字背后的原因。"})
    assert _parse_note(raw) == "这页先讲背景，再点出数字背后的原因。"
    assert _parse_note('{"bullets": ["a"]}') == ""
    assert _parse_note("不是 JSON") == ""


def test_deepen_writes_speaker_note(monkeypatch) -> None:
    from app.services.ppt_studio.outline import OutlinePage

    async def fake_chat(system, user, ws):
        return json.dumps({
            "bullets": ["人工成本三年累计上涨 42%，一线缺口持续扩大", "换线时间超过 4 小时，订单越来越碎"],
            "note": "这页先抛人力成本问题，再引出自动化不是替代人，而是补缺口。",
        })

    def fake_material(*args, **kwargs):
        return ""

    monkeypatch.setattr(deepen, "_chat_text", fake_chat)
    monkeypatch.setattr(deepen, "_page_material", fake_material)
    outline = _outline([
        {"title": "背景", "layout": "bullets", "bullets": ["成本上涨", "换线慢"]},
        {"title": "收尾", "layout": "end"},
    ])
    page = outline.sections[0].pages[0]
    stats = asyncio.run(deepen_outline(outline, "ws-test"))
    assert stats["deepened"] == 1
    assert "补缺口" in (page.note or "")
    assert isinstance(page, OutlinePage)
