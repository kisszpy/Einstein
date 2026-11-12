"""设计方向：内容决定气质，代码保证对比度。

这一层的价值是「一份稿子一个世界」，而不是从六套预置里挑；它的风险也很明确——
让模型直接写十六进制会写出一堆低对比的糊色。所以这里既测「不同」，也测「不出事」。
"""

from __future__ import annotations

import json

import pytest

from app.services.ppt_studio import designs
from app.services.ppt_studio.htmlkit import color, direction as d, tokens as tk
from app.services.ppt_studio.outline import Outline


def _outline(title: str = "2026 智能工厂年度复盘") -> Outline:
    return Outline.model_validate({
        "title": title,
        "subtitle": "五项指标全部达成",
        "audience": "公司管理层",
        "sections": [
            {"title": "指标", "pages": [
                {"title": "联网率 91%", "layout": "stat", "bullets": ["3,200 台设备联网，三年补完"]},
                {"title": "换线 19 分钟", "layout": "bullets", "bullets": ["换线时间从 48 分钟压到 19 分钟"]},
            ]},
        ],
    })


# ---------------------------------------------------------------- 解析与校验


def test_parse_accepts_a_well_formed_direction():
    text = json.dumps({
        "name": "量具间", "thesis": "从量具间取质感", "mode": "dark",
        "hue": 36, "chroma": 0.3, "hue2": 202, "density": "tight", "motif": "rule",
    })
    parsed = d.parse(f"说明：\n```json\n{text}\n```")
    assert parsed is not None
    assert parsed.name == "量具间" and parsed.hue == 36 and parsed.motif == "rule"


@pytest.mark.parametrize("patch", [
    {"mode": "sepia"},
    {"density": "dense"},
    {"motif": "stars"},
    {"chroma": 0.9},
    {"hue": 40, "hue2": 70},          # 两个色相太近，图表色带会糊
    {"name": ""},
])
def test_parse_rejects_out_of_range(patch):
    base = {"name": "x", "thesis": "", "mode": "dark", "hue": 200, "chroma": 0.3,
            "hue2": 20, "density": "normal", "motif": "rule"}
    base.update(patch)
    assert d.parse(json.dumps(base)) is None


def test_parse_survives_garbage():
    assert d.parse("我觉得可以从钢尺取色") is None
    assert d.parse('{"name": "x"') is None


# ---------------------------------------------------------------- 确定性


def test_same_outline_gets_the_same_world():
    assert d.seed_of(_outline()) == d.seed_of(_outline())
    assert d.guess(_outline()).payload() == d.guess(_outline()).payload()


def test_different_content_gets_a_different_world():
    """这正是「不再千篇一律」要证明的东西。"""
    left = d.guess(_outline("2026 智能工厂年度复盘"))
    right = d.guess(_outline("2026 零售渠道复盘"))
    assert (left.hue, left.mode, left.motif) != (right.hue, right.mode, right.motif)


def test_seed_knob_rerolls_the_world():
    base = _outline()
    base.style.seed = 0
    first = d.seed_of(base)
    base.style.seed = 7
    assert d.seed_of(base) != first


def test_derived_hues_stay_out_of_the_purple_band():
    """250~320 是检测器明令的 AI 指纹，派生的副色相不能落进去。"""
    for hue in range(0, 360, 7):
        second = d._second_hue(hue, 3)
        assert not 250 <= second <= 320, (hue, second)
        gap = abs(second - hue) % 360
        assert min(gap, 360 - gap) >= 40, (hue, second)


# ---------------------------------------------------------------- 对比度


def test_every_synthesized_direction_clears_the_contrast_floor():
    """720 组参数全扫：正文/次要/强调/图表都要达标。

    方向可以随便选，但「看不清」不能是选出来的结果——这是把物理约束留在代码里
    的意义，检测器只当复核。
    """
    for mode in d.MODES:
        for hue in range(0, 360, 30):
            for chroma in (0.10, 0.30, 0.52):
                preset = d.Direction(
                    "k", "k", "", mode, hue, chroma,
                    d._second_hue(hue, 1), "normal", "rule",
                ).preset()
                tokens = tk.resolve_tokens(preset)
                assert not _violations(tokens), (mode, hue, chroma, _violations(tokens))


def _violations(tokens: dict) -> dict:
    colors = tokens["colors"]
    checks = {
        "ink": color.contrast(colors["ink"], colors["bg"]),
        "muted": color.contrast(colors["muted"], colors["bg"]),
        "faint": color.contrast(colors["faint"], colors["bg"]),
        "accent": color.contrast(colors["accent_ink"], colors["bg"]),
        "card": color.contrast(colors["ink"], colors["card"]),
    }
    bad = {key: round(value, 2) for key, value in checks.items() if value < 4.5}
    if checks["ink"] < 10:
        bad["ink"] = round(checks["ink"], 2)
    chart = min(color.contrast(hue, colors["card"]) for hue in tokens["chart"])
    if chart < 3:
        bad["chart"] = round(chart, 2)
    return bad


def test_synthesized_preset_keeps_the_chart_ramp_hue_distinct():
    preset = d.Direction("k", "k", "", "dark", 208, 0.34, 24, "normal", "grid").preset()
    chart = preset["family"]["chart"]
    assert len(chart) == 7
    assert len(set(chart)) >= 5, chart


# ---------------------------------------------------------------- 选择入口


def test_auto_applies_only_when_no_style_was_chosen():
    assert designs.wants_auto("", "")
    assert designs.wants_auto(designs.AUTO_ID, "")
    assert not designs.wants_auto("builtin:midnight", "")
    assert not designs.wants_auto("tp_abc", "")
    assert not designs.wants_auto("", "ocean")


def test_preset_for_builds_the_direction_from_the_outline(monkeypatch):
    from app.services.ppt_studio import theme_pack

    monkeypatch.setattr(d, "ask", lambda outline, ws: d.guess(outline))
    monkeypatch.setattr(d, "_llm_enabled", lambda: True)
    outline = _outline()
    preset = theme_pack.preset_for("", "", "default", outline=outline, session_id="")
    assert preset["key"].startswith("auto:")
    assert preset["direction"]["name"]
    assert preset["palette"]["bg"] != designs.default_preset()["palette"]["bg"]


def test_resolve_reuses_the_cached_direction_for_the_same_outline(monkeypatch):
    from app.services.ppt_studio import session_store

    calls: list[int] = []
    store = {"design_direction": None}

    def _get(session_id):
        payload = {"id": session_id}
        if store["design_direction"]:
            payload["design_direction"] = store["design_direction"]
        return payload

    def _update(session_id, patch):
        store["design_direction"] = patch.get("design_direction")
        return {"id": session_id, **patch}

    monkeypatch.setattr(session_store, "get_session", _get)
    monkeypatch.setattr(session_store, "update_session", _update)
    monkeypatch.setattr(d, "ask", lambda outline, ws: calls.append(1) or d.guess(outline))
    monkeypatch.setattr(d, "_llm_enabled", lambda: True)

    first = d.resolve_for(_outline(), "ps_x", "default")
    second = d.resolve_for(_outline(), "ps_x", "default")
    assert first.payload() == second.payload()
    assert len(calls) == 1          # 第二次命中缓存，没有再调模型
    assert store["design_direction"]["fingerprint"] == d.seed_of(_outline())