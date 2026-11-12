"""确认卡校验与套母版生成：校验码、页数口径、落盘 dedupe、字体三件套。

generate 是纯同步函数，直接函数级测试；文件落盘指到 tmp_path，不碰真实项目。
"""

from __future__ import annotations

import io

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from app.services.capabilities import sandbox
from app.services import projects as project_registry
from app.services.ppt_studio import generator, session_store, theme_pack
from app.services.ppt_studio.outline import Outline, ensure_valid

WS = "ws-gen"


def _deep(shapes):
    """HTML 引擎把整页装进组合形状，GroupShape.has_text_frame 恒为 False。"""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _deep(shape.shapes)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    session_store._tables()["sessions"].delete_where("1=1")
    session_store._tables()["messages"].delete_where("1=1")
    session_store._tables()["generations"].delete_where("1=1")
    theme_pack._table().delete_where("1=1")
    yield
    session_store._tables()["sessions"].delete_where("1=1")
    session_store._tables()["messages"].delete_where("1=1")
    session_store._tables()["generations"].delete_where("1=1")
    theme_pack._table().delete_where("1=1")


def _outline_dict(pages: int = 3, theme_pack_id: str = "", sources: bool = False) -> dict:
    pages_items: list[dict] = [{"title": "开场", "layout": "cover"}]
    for i in range(max(pages - 2, 0)):
        pages_items.append({"title": f"要点 {i + 1}", "layout": "bullets", "bullets": [f"论点 {i + 1}"]})
    pages_items.append({"title": "收尾", "layout": "end"})
    # 每章最多 8 页：按 8 页一章拆开（31 页 → 8+8+8+7）
    sections = [
        {"title": f"第 {i + 1} 章", "pages": pages_items[i:i + 8]}
        for i in range(0, len(pages_items), 8)
    ]
    data: dict = {
        "title": "季度经营复盘",
        "subtitle": "2026 Q3",
        "sources_policy": "both" if sources else "off",
        "sections": sections,
    }
    if theme_pack_id:
        data["style"] = {"theme_pack_id": theme_pack_id}
    if sources:
        data["sources"] = [
            {"id": "w1", "kind": "web", "title": "行业报告", "url": "https://example.com/a"},
        ]
        sections[0]["pages"][1]["sources"] = [{"id": "w1"}]
    return data


def _session(project: str = "demo") -> dict:
    return session_store.create_session(workspace_id=WS, owner_id="u1", project=project)


def _project(owner_id: str = "u1") -> str:
    info = project_registry.create_project(WS, "demo", owner_id=owner_id)
    return info["project"]


# ---------------------------------------------------------------- 校验


def test_validate_rejects_title_and_page_range():
    bad = Outline.model_validate(_outline_dict()).normalize()
    bad.title = "  "
    with pytest.raises(ValueError, match="title_required"):
        ensure_valid(bad)
    # 2 页 < 下限
    with pytest.raises(ValueError, match="page_count_range"):
        ensure_valid(Outline.model_validate(_outline_dict(pages=2)).normalize())
    # 31 页 > 上限
    with pytest.raises(ValueError, match="page_count_range"):
        ensure_valid(Outline.model_validate(_outline_dict(pages=31)).normalize())


def test_bullets_over_limit_warns_not_blocks():
    data = _outline_dict(pages=3)
    data["sections"][0]["pages"][1]["bullets"] = [f"第 {i} 条论点内容" for i in range(9)]
    outline = Outline.model_validate(data).normalize()
    ensure_valid(outline)  # 软告警不拦
    assert any("截断" in w for w in outline.warnings())


def test_total_slides_counts_appendix():
    plain = Outline.model_validate(_outline_dict(sources=False)).normalize()
    assert plain.total_slides() == plain.content_page_count()
    with_ref = Outline.model_validate(_outline_dict(sources=True)).normalize()
    # 口径：total_slides = 内容页 + 来源附录页（来源附录不计入 page_count）
    assert with_ref.has_appendix() is True
    assert with_ref.total_slides() == with_ref.content_page_count() + 1


# ---------------------------------------------------------------- 生成


def test_generate_with_template_keeps_its_ratio_and_stays_editable():
    """上传模板走风格迁移：抽出的美术方向交给 HTML 引擎重绘，比例仍是模板的。"""
    raw = io.BytesIO()
    Presentation().save(raw)  # python-pptx 内置模板是 4:3
    pack = theme_pack.create_pack(workspace_id=WS, owner_id="u1", raw=raw.getvalue(), filename="t.pptx")
    outline = Outline.model_validate(_outline_dict(pages=4, theme_pack_id=pack["id"], sources=True)).normalize()
    ensure_valid(outline)
    payload = generator.generate_deck(_session(), outline, _project(), WS, "u1")

    assert payload["status"] == "ok"
    deck = payload["deck"]
    assert deck["path"].startswith("deck/")
    assert deck["ticket"] and "ticket=" in deck["download_url"]
    assert payload["total_slides"] == outline.total_slides()
    assert payload["fidelity"]["engine"] == "template"

    target = sandbox.workspace_root(WS) / "demo" / deck["path"]
    assert target.is_file()
    prs = Presentation(str(target))
    assert len(prs.slides._sldIdLst) == payload["total_slides"]
    assert (prs.slide_width, prs.slide_height) == (9144000, 6858000), "4:3 模板必须出 4:3 成稿"
    # 字体三件套：正文 run 必须显式带 font.name（母版继承之外还要 latin 名）
    runs = [
        run
        for shape in _deep(prs.slides[1].shapes)
        if shape.has_text_frame
        for para in shape.text_frame.paragraphs
        for run in para.runs
        if run.text.strip()
    ]
    assert runs and all(run.font.name for run in runs)


def test_template_without_browser_falls_back_to_master_layout_with_a_note(monkeypatch):
    """浏览器不可用时回落母版套版：比例仍是模板的，但必须明说排版降级了。"""
    from app.services.ppt_studio.htmlkit import renderer as html_renderer

    monkeypatch.setattr(html_renderer, "available", lambda: False)
    monkeypatch.setattr(html_renderer, "unavailable_reason", lambda: "没有 node")
    raw = io.BytesIO()
    Presentation().save(raw)
    pack = theme_pack.create_pack(workspace_id=WS, owner_id="u1", raw=raw.getvalue(), filename="t.pptx")
    outline = Outline.model_validate(_outline_dict(pages=3, theme_pack_id=pack["id"])).normalize()
    payload = generator.generate_deck(_session(), outline, _project(), WS, "u1")

    assert payload["fidelity"]["engine"] == "template-shapes"
    notes = payload["fidelity"]["degradations"]
    assert any("回落模板母版套版" in note for note in notes), notes
    # 只有真的套母版时，解析期的占位符降级才算降级
    assert set(pack["fidelity"]["degradations"]) <= set(notes)
    target = sandbox.workspace_root(WS) / "demo" / payload["deck"]["path"]
    prs = Presentation(str(target))
    assert (prs.slide_width, prs.slide_height) == (9144000, 6858000)


def test_template_font_substitution_reaches_the_fidelity_report_once(monkeypatch):
    """字体被换掉这件事要出现在结果卡上，而且不能重复两遍。"""
    from app.services.ppt_studio import template_style

    real = template_style.extract

    def picky(*args, **kwargs):
        kwargs["fonts"] = {**kwargs["fonts"], "minor_ea": "阿里巴巴普惠体 3.0"}
        return real(*args, **kwargs)

    monkeypatch.setattr(template_style, "extract", picky)
    raw = io.BytesIO()
    Presentation().save(raw)
    pack = theme_pack.create_pack(workspace_id=WS, owner_id="u1", raw=raw.getvalue(), filename="t.pptx")
    outline = Outline.model_validate(_outline_dict(pages=3, theme_pack_id=pack["id"])).normalize()
    payload = generator.generate_deck(_session(), outline, _project(), WS, "u1")

    notes = payload["fidelity"]["degradations"]
    hits = [note for note in notes if "阿里巴巴普惠体" in note]
    assert len(hits) == 1, notes


def test_redraw_report_describes_the_render_not_the_master_parse():
    """重绘的成稿摘要不能说母版解析结论。

    风格迁移压根不往占位符里填空，把「11 个版式完整解析，无降级项」搬到结果卡上，
    既盖掉了真正的渲染结论（实测排版 / 逐页体检），还和下面挂着的字体替换自相矛盾。
    出处要单独存一份：模板日后被删，历史结果卡仍要说清当时按谁的风格画的。
    """
    raw = io.BytesIO()
    Presentation().save(raw)
    pack = theme_pack.create_pack(workspace_id=WS, owner_id="u1", raw=raw.getvalue(), filename="t.pptx")
    outline = Outline.model_validate(_outline_dict(pages=3, theme_pack_id=pack["id"])).normalize()
    payload = generator.generate_deck(_session(), outline, _project(), WS, "u1")

    fidelity = payload["fidelity"]
    assert fidelity["engine"] == "template"
    assert "实测排版" in fidelity["summary_zh"], fidelity["summary_zh"]
    assert "完整解析" not in fidelity["summary_zh"]
    assert not set(pack["fidelity"]["degradations"]) & set(fidelity["degradations"])
    assert fidelity["template"] == {
        "name": pack["name"],
        "ratio": "4:3",
        "mode": fidelity["template"]["mode"],
    }


def test_generate_dedupes_same_name_with_suffix():
    session = _session()
    outline = Outline.model_validate(_outline_dict()).normalize()
    first = generator.generate_deck(session, outline, _project(), WS, "u1")
    second = generator.generate_deck(session, outline, _project(), WS, "u1")
    assert first["deck"]["name"] != second["deck"]["name"]
    assert "-2" in second["deck"]["name"]
    # 生成记录两条，会话页数口径落在最后一次
    assert len(session_store.list_generations(session["id"])) == 2


def test_generate_without_template_is_16x9_with_preset_theme():
    outline = Outline.model_validate(_outline_dict()).normalize()
    outline.style.preset_theme = "ocean"
    payload = generator.generate_deck(_session(), outline, _project(), WS, "u1")
    target = sandbox.workspace_root(WS) / "demo" / payload["deck"]["path"]
    prs = Presentation(str(target))
    # HTML 实测渲染按 96dpi 精确对齐：1280px × 9525 = 12192000 EMU（真正的 16:9），
    # 比 Inches(13.333) 多 305 EMU。两条渲染路径都要落在 16:9 上，不比字面英寸数。
    assert prs.slide_width in (Inches(13.333), 12192000)
    assert prs.slide_height == Inches(7.5)
    assert abs(prs.slide_width / prs.slide_height - 16 / 9) < 1e-4


def test_generate_rejects_invisible_theme_pack():
    outline = Outline.model_validate(_outline_dict(theme_pack_id="tp_nope")).normalize()
    with pytest.raises(generator.GenerationError, match="theme_pack_not_found"):
        generator.generate_deck(_session(), outline, _project(), WS, "u1")


def test_generate_rejects_missing_project():
    outline = Outline.model_validate(_outline_dict()).normalize()
    with pytest.raises(sandbox.SandboxError):
        generator.generate_deck(_session(project="ghost"), outline, "ghost", WS, "u1")
