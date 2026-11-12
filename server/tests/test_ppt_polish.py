"""润色层：白名单校验、补丁解析、注入、以及「退步就回退」。

这一层最怕两件事：模型返回的 CSS 把排版弄坏，或者为了消掉一条检测规则把内容
改掉。所以这里既测它「能改什么」，也测它「改坏了会被拦住」。
"""

from __future__ import annotations

import json
import re

import pytest

from app.services.ppt_studio import designs
from app.services.ppt_studio.htmlkit import compose, impeccable, polish, tokens as tk
from app.services.ppt_studio.htmlkit.compose import SlideHtml
from app.services.ppt_studio.htmlkit.renderer import HtmlRenderer
from tests.test_ppt_htmlkit import _outline


def _slides() -> list[SlideHtml]:
    tokens = tk.resolve_tokens(designs.default_preset())
    return compose.compose_deck(_outline(), tokens)


# ---------------------------------------------------------------- 白名单


@pytest.mark.parametrize("css", [
    ".b-text{line-height:1.66}",
    ".card{border-radius:10px;padding:18px}",
    ".card.card--topline{border-top-width:0}",
    ".slide[data-kind=\"two_col\"] .card--topline{border-top:0}",
    ".b-item:nth-of-type(2){padding:12px 0}",
    ".kicker{color:#7987A0;letter-spacing:.06em;font-weight:500}",
    ".b-band{--hue:#14B8C4;border-left-color:var(--hue)}",
    ".card>.b-text{opacity:.9}",
])
def test_sanitize_keeps_mappable_rules(css):
    kept, dropped = polish.sanitize(css)
    assert kept and not dropped, dropped


@pytest.mark.parametrize("css,why", [
    (".card{transform:scale(1.2)}", "transform 不在白名单"),
    (".card{box-shadow:0 2px 8px #000}", "投影映射不到原生形状"),
    (".card{position:absolute;left:0}", "定位会破坏实测映射"),
    (".card{display:grid}", "display 会重排整页"),
    (".evil{color:#fff}", "未知类名"),
    ("body{color:#fff}", "就是标签选择器"),
    ("div.card{color:#fff}", "带标签名"),
    (".card{color:url(x)}", "url()"),
    (".card{color:#fff!important}", "!important"),
    ("@media print{.card{color:#fff}}", "@ 规则"),
    (".b-text{font-size:9px}", "字号低于可用下限"),
    (".b-text{line-height:3}", "行高过松"),
    (".b-item{padding:200px}", "内边距超出预算"),
    (".card{border-radius:64px}", "圆角超出预算"),
])
def test_sanitize_rejects_anything_that_can_break_the_deck(css, why):
    kept, dropped = polish.sanitize(css)
    assert not kept, why
    assert dropped, why


def test_sanitize_drops_only_the_bad_block():
    """一条坏声明不该让整份补丁作废——模型写对的那部分还要能用。"""
    kept, dropped = polish.sanitize(".card{color:#fff;filter:blur(2px)}.b-text{line-height:1.6}")
    assert kept == ".card{color:#fff};.b-text{line-height:1.6}"
    assert any("filter" in reason for reason in dropped)


def test_selector_for_the_model_strips_tag_names():
    """检测器给的是 `div.card:nth-of-type(1)`，直接抄进 CSS 会被白名单拒。"""
    assert polish.usable_selector("div.card:nth-of-type(1)") == ".card:nth-of-type(1)"
    assert polish.usable_selector(".b-text") == ".b-text"
    assert polish.usable_selector("section .quote-text") == "section .quote-text"


# ---------------------------------------------------------------- 解析


def test_parse_reads_page_numbers_one_based():
    """手册里写的是「index 从 1 开始」，模型照做时不能偏一页。"""
    patch = polish.parse(json.dumps({
        "global_css": ".b-text{line-height:1.66}",
        "pages": [{"index": 2, "css": ".b-item{padding:12px 0}"}],
    }), slide_count=5)
    assert patch.global_css == ".b-text{line-height:1.66}"
    assert patch.pages == {1: ".b-item{padding:12px 0}"}


def test_parse_follows_zero_based_output_as_a_batch():
    """出现 0 就说明整批按 0 起编，不能把第 5 页当第 6 页改。"""
    patch = polish.parse(json.dumps({
        "pages": [{"index": 0, "css": ".b-item{padding:10px 0}"},
                  {"index": 3, "css": ".b-item{padding:14px 0}"}],
    }), slide_count=5)
    assert patch.pages == {0: ".b-item{padding:10px 0}", 3: ".b-item{padding:14px 0}"}


def test_parse_rejects_out_of_range_and_garbage():
    patch = polish.parse(json.dumps({
        "pages": [{"index": 99, "css": ".b-item{padding:10px 0}"}],
        "deferred": [{"index": 1, "rule": "x", "reason": "需要删元素"}],
    }), slide_count=4)
    assert patch.pages == {}
    assert patch.deferred[0]["reason"] == "需要删元素"
    assert any("越界" in reason for reason in patch.rejected)


def test_parse_survives_fenced_json_and_plain_prose():
    fenced = polish.parse("说明如下：\n```json\n{\"global_css\": \".body{line-height:1.6}\"}\n```", slide_count=3)
    assert fenced.global_css == ".body{line-height:1.6}"
    assert polish.parse("我觉得挺好的", slide_count=3).rejected


# ---------------------------------------------------------------- 注入


def _strip_polish(html: str) -> str:
    return re.sub(r'<style data-polish="1">.*?</style>', "", html)


def test_apply_injects_only_where_the_patch_points():
    slides = _slides()
    patch = polish.Patch(global_css=".b-text{line-height:1.66}", pages={2: ".b-item{padding:12px 0}"})
    out = polish.apply(slides, patch)
    assert out[0].html.count("data-polish") == 1          # 只有全篇补丁
    assert ".b-item{padding:12px 0}" in out[2].html
    assert ".b-item{padding:12px 0}" not in out[1].html
    # 类名与结构不许动，否则实测与 pptx 映射会错位
    for before, after in zip(slides, out):
        assert _strip_polish(after.html) == before.html


def test_apply_is_a_no_op_for_an_empty_patch():
    slides = _slides()
    assert polish.apply(slides, polish.Patch()) is slides


# ---------------------------------------------------------------- 提示词


def test_brief_sends_the_design_card_not_the_whole_stylesheet():
    slides = _slides()
    findings = [impeccable.Finding(rule="low-contrast", name="对比度不足", detail="2.9:1",
                                   selector="div.foot-src", slide=1)]
    brief = polish.build_brief(tk.resolve_tokens(designs.default_preset()), slides, findings)
    assert "画布 1280×720px" in brief
    assert "对比度不足" in brief and ".foot-src" in brief
    assert "@keyframes" not in brief
    assert brief.count(":root{") == 0
    assert len(brief) < 12000


# ---------------------------------------------------------------- 回退


def _renderer() -> HtmlRenderer:
    return HtmlRenderer(designs.default_preset(), workspace_id="ws-test")


def test_patch_that_loses_text_is_reverted(monkeypatch):
    """内容丢失一律回退。润色只准更好看，不准少一个字。"""
    slides = _slides()
    tokens = tk.resolve_tokens(designs.default_preset())
    from app.services.ppt_studio.htmlkit import lint, measure

    original = measure.measure(slides, __import__("pathlib").Path(
        __import__("tempfile").mkdtemp(prefix="polish-test-")), canvas=(1280, 720))
    by_index = {int(item["index"]): item for item in original}
    reports = {i: lint.inspect(item, tokens, layout=slides[i].kind) for i, item in by_index.items()}

    def lossy(slides_in, out_dir, **kwargs):
        out = []
        for item in original:
            copy = {**item, "nodes": [n for n in item["nodes"] if n.get("className") != "b-text"]}
            out.append(copy)
        return out

    monkeypatch.setattr(measure, "measure", lossy)
    result = _renderer()._try_patch(
        slides, by_index, reports, polish.Patch(pages={1: ".b-item{padding:12px 0}"}),
        __import__("pathlib").Path(__import__("tempfile").mkdtemp()), 0,
    )
    assert result is None


def test_request_degrades_to_an_empty_patch_when_the_model_fails(monkeypatch):
    monkeypatch.setattr(polish, "_ask", lambda brief, ws, system: (_ for _ in ()).throw(RuntimeError("boom")))
    patch = polish.request(tk.resolve_tokens(designs.default_preset()), _slides(),
                           [impeccable.Finding(rule="x", name="x", detail="x", slide=0)], "ws")
    assert patch.empty
    assert any("模型调用失败" in reason for reason in patch.rejected)


def test_detector_reports_a_reason_instead_of_raising(monkeypatch):
    monkeypatch.setattr(impeccable, "locate", lambda: None)
    report = impeccable.inspect(_slides(), __import__("pathlib").Path("."), canvas=(1280, 720))
    assert not report.findings
    assert "fetch" in report.skipped


def test_detector_curates_web_only_rules_and_keeps_slide_ones(monkeypatch):
    """网页专属规则（脚本报错、动画）在幻灯片上恒定误报，必须在适配层就丢掉。"""
    raw = [
        {"antipattern": "script-error", "selector": "body", "snippet": "x", "description": "x", "file": "00.html"},
        {"antipattern": "bounce-easing", "selector": ".a", "snippet": "x", "description": "x", "file": "00.html"},
        {"antipattern": "low-contrast", "selector": "div.foot-src", "snippet": "3.1:1",
         "description": "Low contrast text. More detail.", "file": "00.html"},
    ]

    class _Proc:
        returncode = 2
        stdout = json.dumps(raw)
        stderr = ""

    monkeypatch.setattr(impeccable, "locate", lambda: __import__("pathlib").Path("bin"))
    monkeypatch.setattr(impeccable.subprocess, "run", lambda *a, **k: _Proc())
    report = impeccable.inspect([SlideHtml(index=0, kind="bullets", title="t", html="<div></div>")],
                                __import__("pathlib").Path(__import__("tempfile").mkdtemp()),
                                canvas=(1280, 720))
    rules = {finding.rule for finding in report.findings}
    assert rules == {"low-contrast"}
    finding = report.findings[0]
    assert finding.slide == 0 and finding.severity == "error"
    assert finding.name == "对比度不足"

def test_verification_crash_keeps_the_draft(monkeypatch):
    """复核阶段浏览器炸了，必须保住初稿。

    异常一旦漏出去，上层会把它当成「实测排版失败」整份降级成手写坐标——
    为一次润色丢掉全部实测排版，代价完全不成比例。
    """
    from app.services.ppt_studio.htmlkit import measure

    slides = _slides()
    tokens = tk.resolve_tokens(designs.default_preset())

    def boom(*args, **kwargs):
        raise measure.MeasureError("chromium died")

    monkeypatch.setattr(measure, "measure", boom)
    result = _renderer()._try_patch(
        slides, {}, {}, polish.Patch(pages={1: ".b-item{padding:12px 0}"}),
        __import__("pathlib").Path(__import__("tempfile").mkdtemp()), 0,
    )
    assert result is None


# ---------------------------------------------------------------- 技能接入


def test_skill_bridge_reads_the_installed_skill_and_its_routed_reference(tmp_path, monkeypatch):
    """润色的设计判断来自已安装的技能，不是代码里的另一份提示词。

    技能是路由：SKILL.md 正文 + 本轮命令的 reference + 编辑前必读的品质地板。
    缺了后两个，模型就只知道「有个设计技能」而不知道这轮该怎么做。
    """
    from app.services import skill_publisher, skills as skills_mod
    from app.services.ppt_studio.htmlkit import skill_bridge

    root = tmp_path / "skill-gh-test"
    (root / "reference").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\n---\n\n技能正文：按命令分派", encoding="utf-8"
    )
    (root / "reference" / "polish.md").write_text("润色命令的步骤", encoding="utf-8")
    (root / "reference" / "craft-floor.md").write_text("品质地板：标签上方不许加小标", encoding="utf-8")

    class _Skill:
        id = "gh-test"
        repo = "pbakaus/impeccable"
        name = "impeccable"
        commit = "abc123456789"
        enabled = True

    class _Store:
        def list_skills(self, *args, **kwargs):
            return [_Skill()]

    monkeypatch.setattr(skills_mod, "get_skill_store", lambda: _Store())
    monkeypatch.setattr(skill_publisher, "skills_dir", lambda ws: tmp_path)
    monkeypatch.setattr(skill_publisher, "slug_of", lambda skill: "skill-gh-test")

    ctx = skill_bridge.resolve("default")
    assert ctx is not None
    assert ctx.command == "polish"
    assert "技能正文" in ctx.system
    assert "润色命令的步骤" in ctx.system
    assert "品质地板" in ctx.system
    assert "name: x" not in ctx.system          # frontmatter 不进提示词
    assert ctx.commit == "abc123456789"
    assert set(ctx.files) == {"SKILL.md", "reference/polish.md", "reference/craft-floor.md"}


def test_skill_bridge_tolerates_a_skill_missing_its_references(tmp_path, monkeypatch):
    """参考文件缺失只降级不报错：技能正文仍然可用。"""
    from app.services import skill_publisher, skills as skills_mod
    from app.services.ppt_studio.htmlkit import skill_bridge

    root = tmp_path / "skill-gh-solo"
    root.mkdir()
    (root / "SKILL.md").write_text("只有正文", encoding="utf-8")

    class _Skill:
        repo = "pbakaus/impeccable"
        name = "impeccable"
        commit = "c0ffee"
        enabled = True

    class _Store:
        def list_skills(self, *args, **kwargs):
            return [_Skill()]

    monkeypatch.setattr(skills_mod, "get_skill_store", lambda: _Store())
    monkeypatch.setattr(skill_publisher, "skills_dir", lambda ws: tmp_path)
    monkeypatch.setattr(skill_publisher, "slug_of", lambda skill: "skill-gh-solo")

    ctx = skill_bridge.resolve("default")
    assert ctx is not None and ctx.files == ("SKILL.md",)
    assert "只有正文" in ctx.system


def test_skill_bridge_returns_none_when_nothing_is_installed(monkeypatch):
    from app.services.ppt_studio.htmlkit import skill_bridge
    from app.services import skills as skills_mod

    class _Empty:
        def list_skills(self, *args, **kwargs):
            return []

    monkeypatch.setattr(skills_mod, "get_skill_store", lambda: _Empty())
    assert skill_bridge.resolve("default") is None


def test_composed_system_keeps_the_engine_contract_after_the_skill():
    """技能在前、介质适配与机器契约在后。

    顺序有讲究：契约是最后的硬约束，放在前面会被技能里「大胆一点」那类
    建议盖过去，模型就会写出 transform / 改结构。
    """
    from app.services.ppt_studio.htmlkit import playbook

    system = playbook.compose_system("SKILL BODY")
    assert system.index("SKILL BODY") < system.index("机器契约")
    assert "只输出一个 JSON 对象" in system
    assert "transform" in system
    assert "从 1 开始" in system


def test_patch_records_which_skill_drove_it(monkeypatch):
    from app.services.ppt_studio.htmlkit import skill_bridge

    class _Ctx:
        system = "SKILL BODY"

        def payload(self):
            return {"name": "impeccable", "version": "deadbeef", "command": "polish"}

    monkeypatch.setattr(skill_bridge, "resolve", lambda *a, **k: _Ctx())
    monkeypatch.setattr(polish, "_ask", lambda brief, ws, system: '{"global_css": ".b-text{line-height:1.66}"}')
    patch = polish.request(
        tk.resolve_tokens(designs.default_preset()), _slides(),
        [impeccable.Finding(rule="x", name="x", detail="x", slide=0)], "ws",
    )
    assert patch.skill["name"] == "impeccable"
    assert patch.global_css == ".b-text{line-height:1.66}"


# ---------------------------------------------------------------- 逐页重排


def test_recompose_rejects_anything_outside_the_vocabulary():
    from app.services.ppt_studio.htmlkit import recompose

    ok, why = recompose.validate('<div class="cols grow"><div class="col">正文</div></div>', ["正文"])
    assert ok, why
    for bad, reason in [
        ('<div class="cols" style="width:10px">正文</div>', "style"),
        ('<div class="my-own-class">正文</div>', "类名"),
        ('<section>正文</section>', "标签"),
        ('<script>alert(1)</script>', "标签"),
    ]:
        passed, detail = recompose.validate(bad, ["正文"])
        assert not passed and reason in detail or not passed, (bad, detail)


def test_recompose_rejects_dropped_or_rewritten_text():
    from app.services.ppt_studio.htmlkit import recompose

    bullets = ["联网率 62%→91%", "换线时间压到 19 分钟"]
    assert recompose.validate('<div class="body">联网率 62%→91% 换线时间压到 19 分钟</div>', bullets)[0]
    assert not recompose.validate('<div class="body">联网率大幅提升</div>', bullets)[0]


def test_recompose_falls_back_when_the_page_breaks(monkeypatch):
    """重排后体检退步或文字丢失，一律保留合成器版本。"""
    from app.services.ppt_studio.htmlkit import recompose, renderer

    slides = _slides()
    tokens = tk.resolve_tokens(designs.default_preset())
    from app.services.ppt_studio.htmlkit import lint, measure
    import pathlib, tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="rec-"))
    base = measure.measure(slides, tmp / "base", canvas=(1280, 720))
    by_index = {int(i["index"]): i for i in base}
    reports = {i: lint.inspect(item, tokens, layout=slides[i].kind) for i, item in by_index.items()}
    pages = _outline().flatten_pages()

    # 模型返回了一个结构合法、但内容多到必然溢出的重排（几何退步 → 回退）
    flood = '<div class="body">' + ("量化收益与流程改造的细节" * 60) + "</div>"
    monkeypatch.setattr(recompose, "design_page", lambda *a, **k: (flood, "x"))
    monkeypatch.setattr(renderer, "get_settings",
                        lambda: type("S", (), {"ppt_skill_layout": True})())
    out = HtmlRenderer(designs.default_preset(), workspace_id="ws")._recompose(
        slides, by_index, reports, pages, type("P", (), {"variants": [0] * 20})(), _outline(), tmp, lambda *a: None
    )
    new_slides, _, _, info = out
    assert new_slides is slides
    assert info["skill_pages"] == []
    assert info["fallbacks"]
