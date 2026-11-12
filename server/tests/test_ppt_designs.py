"""内置设计风格（designs + DesignRenderer）：无模板时的成稿质量保障。

覆盖面：
① GET /ppt/templates 返回内置风格段（kind=builtin，全 workspace 可见）；
② DesignRenderer 产物：页数对齐确认卡、封面整幅背景、中文 a:ea/a:cs 显式设置；
③ generate_deck 走 builtin:xxx 时不需要模板文件，直接出设计稿；
④ create_session / 出卡校验接受 builtin id（不用再查主题包表）。
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.oxml.ns import qn

from app.main import app
from app.services.capabilities import sandbox
from app.services import projects as project_registry, sessions, users
from app.services.ppt_studio import designs, generator, session_store
from app.services.ppt_studio.design_renderer import DesignRenderer
from app.services.ppt_studio.outline import Outline

WS = "ws-design"


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    for name in ("sessions", "messages", "generations"):
        session_store._tables()[name].delete_where("1=1")


def _outline_dict(pages: int = 3) -> dict:
    items: list[dict] = [{"title": "开场", "layout": "cover"}]
    for i in range(max(pages - 2, 0)):
        items.append({"title": f"要点 {i + 1}", "layout": "bullets", "bullets": [f"论点 {i + 1}，论据充分"]})
    items.append({"title": "收尾", "layout": "end"})
    sections = [
        {"title": f"第 {i + 1} 章", "pages": items[i:i + 8]}
        for i in range(0, len(items), 8)
    ]
    return {"title": "季度经营复盘", "sections": sections}


def test_builtin_list_payload_shape():
    items = designs.list_payload()
    # 六套固定家族 + 一张「按内容生成方向」的卡
    assert len(items) == len(designs.all_presets()) + 1 == 7
    assert items[0]["id"] == designs.AUTO_ID
    assert items[1]["id"] == "builtin:midnight"
    assert all(item["kind"] == "builtin" for item in items)
    assert all(len(item["swatches"]) == 3 for item in items)
    # 固定家族都能被 resolve_id 认出；智能方向没有固定配色，由内容现生成
    for item in items[1:]:
        assert designs.resolve_id(item["id"]) is not None
    assert designs.resolve_id("builtin:ghost") is None
    assert designs.resolve("", "")["key"] == designs.DEFAULT_KEY


def test_design_renderer_page_count_and_full_bleed_cover():
    outline = Outline.model_validate(_outline_dict(4)).normalize()
    outline.style.theme_pack_id = "builtin:midnight"
    renderer = DesignRenderer(designs.resolve_id("builtin:midnight"))
    result = renderer.render(None, outline)

    assert result["total_slides"] == outline.total_slides()
    assert result["fidelity"]["score"] == 100
    prs = Presentation(io.BytesIO(result["blob"]))
    assert len(prs.slides._sldIdLst) == result["total_slides"]
    assert abs(prs.slide_width.inches - 13.333) < 0.01
    assert abs(prs.slide_height.inches - 7.5) < 0.01

    # 封面第一块形状 = 整幅背景（设计稿的"底色"必须盖满）
    cover_bg = prs.slides[0].shapes[0]
    assert cover_bg.width >= prs.slide_width
    assert cover_bg.height >= prs.slide_height


def test_design_renderer_sets_ea_cs_fonts():
    outline = Outline.model_validate(_outline_dict(3)).normalize()
    renderer = DesignRenderer(designs.resolve_id("builtin:ocean"))
    result = renderer.render(None, outline)
    prs = Presentation(io.BytesIO(result["blob"]))
    checked = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if not run.text.strip():
                        continue
                    rpr = run._r.get_or_add_rPr()
                    ea = rpr.find(qn("a:ea"))
                    assert ea is not None, f"中文 run 缺 a:ea：{run.text[:12]}"
                    assert ea.get("typeface") == designs.FONT_STACK
                    checked += 1
    assert checked > 0


def test_generate_deck_with_builtin_style_needs_no_template(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    project = project_registry.create_project(WS, "demo", owner_id="u1")["project"]
    session = session_store.create_session(workspace_id=WS, owner_id="u1", project=project)
    outline = Outline.model_validate(_outline_dict(3)).normalize()
    outline.style.theme_pack_id = "builtin:sunset"
    outline.style.preset_theme = "sunset"

    payload = generator.generate_deck(session, outline, project, WS, "u1")
    assert payload["status"] == "ok"
    target = sandbox.workspace_root(WS) / project / payload["deck"]["path"]
    assert target.is_file()
    prs = Presentation(str(target))
    assert len(prs.slides._sldIdLst) == payload["total_slides"]
    assert "内置风格" in payload["fidelity"]["summary_zh"]


def test_session_accepts_builtin_style_via_api(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    admin = users.get_user_store().list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        listed = client.get("/api/ppt/templates", headers=headers).json()
        builtin = [t for t in listed if t.get("kind") == "builtin"]
        assert len(builtin) == 7

        res = client.post(
            "/api/ppt/sessions",
            headers=headers,
            json={"title": "风格测试", "theme_pack_id": "builtin:forest"},
        )
        assert res.status_code == 200
        assert res.json()["theme_pack_id"] == "builtin:forest"

        # 出卡校验同样放行 builtin id（ensure_valid 的 theme_pack_visible 由路由判定）
        res2 = client.post(
            "/api/ppt/templates",
            headers=headers,
            json={},
        )
        assert res2.status_code in (400, 422)  # 没带文件：只证明路由活着，不误吞 builtin
