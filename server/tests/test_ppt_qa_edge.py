"""QA 补充边界测试（Edward）：堵住既有用例没盖住的关键路径。

覆盖面（对照 ARCHITECTURE.md 验收与 PRD P0）：
① 模板超过 32MB 上限被拒（template_too_large）；
② 跨 workspace 主题包 404 / 非 owner 删除 403（HTTP 层隔离）；
③ 同名生成文件 -2/-3 去重且绝不覆盖（第三份拿 -3）；
④ 生成产物中文字体 a:ea/a:cs 必须显式设置（防中文回落宋体）；
⑤ 产物可被 python-pptx 重新打开：页数与确认卡一致、页面文本非空；
⑥ 主题包 / 会话 store 在 json 后端下的兼容性。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn


def _deep(shapes):
    """HTML 引擎把整页装进组合形状，GroupShape.has_text_frame 恒为 False。"""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _deep(shape.shapes)

from app.main import app
from app.services.capabilities import sandbox
from app.services import members, projects as project_registry, sessions, users, workspaces
from app.services.ppt_studio import generator, session_store, theme_pack
from app.services.ppt_studio.outline import Outline
from app.services.state import JsonState

WS = "ws-qa-edge"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    for store in (
        session_store._tables()["sessions"],
        session_store._tables()["messages"],
        session_store._tables()["generations"],
        theme_pack._table(),
    ):
        store.delete_where("1=1")
    yield
    for store in (
        session_store._tables()["sessions"],
        session_store._tables()["messages"],
        session_store._tables()["generations"],
        theme_pack._table(),
    ):
        store.delete_where("1=1")


def _sample_pptx() -> bytes:
    buf = io.BytesIO()
    Presentation().save(buf)
    return buf.getvalue()


def _outline_dict(pages: int = 3) -> dict:
    items: list[dict] = [{"title": "开场", "layout": "cover"}]
    for i in range(max(pages - 2, 0)):
        items.append({"title": f"要点 {i + 1}", "layout": "bullets", "bullets": [f"论点 {i + 1}，结论充分"]})
    items.append({"title": "收尾", "layout": "end"})
    sections = [
        {"title": f"第 {i + 1} 章", "pages": items[i:i + 8]}
        for i in range(0, len(items), 8)
    ]
    return {"title": "季度经营复盘", "sources_policy": "off", "sections": sections}


# ---------------------------------------------------------------- ① 体积上限


def test_template_over_size_limit_rejected(monkeypatch):
    """>32MB 拒绝：monkeypatch 上限到 1KB，省去真造 33MB 载荷。"""
    monkeypatch.setattr(theme_pack, "MAX_TEMPLATE_BYTES", 1024)
    raw = _sample_pptx()
    assert len(raw) > 1024
    admin = users.get_user_store().list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        res = client.post(
            "/api/ppt/templates",
            headers=headers,
            files={"file": ("big.pptx", raw, "application/octet-stream")},
        )
    assert res.status_code == 400
    assert "template_too_large" in res.text


# ---------------------------------------------------------------- ② 隔离（HTTP 层）


@pytest.fixture()
def admin_headers():
    admin = users.get_user_store().list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_template_cross_workspace_is_404(admin_headers):
    """跨 workspace 的主题包：列表不出现、详情/删除一律 404（不泄露存在性）。"""
    ws_other = "ws-qa-cross"
    workspaces.get_workspace_store().add_with_id(ws_other, "QA 跨空间")
    with TestClient(app) as client:
        pack = client.post(
            "/api/ppt/templates",
            headers=admin_headers,
            files={"file": ("brand.pptx", _sample_pptx(), "application/octet-stream")},
        ).json()
        foreign = dict(admin_headers, **{"X-Workspace": ws_other})
        # 内置风格是全局的（designs.list_payload），跨空间断言只看上传的主题包
        foreign_uploads = [
            t for t in client.get("/api/ppt/templates", headers=foreign).json()
            if t.get("kind") != "builtin"
        ]
        assert foreign_uploads == []
        assert client.get(f"/api/ppt/templates/{pack['id']}", headers=foreign).status_code == 404
        assert client.delete(f"/api/ppt/templates/{pack['id']}", headers=foreign).status_code == 404
        # 本空间仍可见（隔离不是误伤）
        assert client.get(f"/api/ppt/templates/{pack['id']}", headers=admin_headers).status_code == 200


def test_template_delete_by_non_owner_is_403(admin_headers):
    """同 workspace 的普通成员：模板可见（共享读），但删除必须 403。"""
    user_store = users.get_user_store()
    member = user_store.create(username="qa-member-403", password="secret123", role="member")
    members.get_member_store().ensure(member.id, workspaces.DEFAULT_ID, role="member")
    token, _ = sessions.get_session_store().issue(member.id)
    member_headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        pack = client.post(
            "/api/ppt/templates",
            headers=admin_headers,
            files={"file": ("shared.pptx", _sample_pptx(), "application/octet-stream")},
        ).json()
        # 共享读：成员能看详情
        assert client.get(f"/api/ppt/templates/{pack['id']}", headers=member_headers).status_code == 200
        # 但删除被拒（只有 owner 或 admin 能删）
        res = client.delete(f"/api/ppt/templates/{pack['id']}", headers=member_headers)
        assert res.status_code == 403
        # owner 本人仍可删
        assert client.delete(f"/api/ppt/templates/{pack['id']}", headers=admin_headers).json()["ok"]


# ---------------------------------------------------------------- ③ 同名去重


def _project(owner_id: str = "u1") -> str:
    return project_registry.create_project(WS, "demo", owner_id=owner_id)["project"]


def test_generate_same_name_three_times_never_overwrites(tmp_path):
    session = session_store.create_session(workspace_id=WS, owner_id="u1", project="demo")
    outline = Outline.model_validate(_outline_dict()).normalize()
    project = _project()
    names = [
        generator.generate_deck(session, outline, project, WS, "u1", filename="复盘")["deck"]["name"]
        for _ in range(3)
    ]
    assert names == ["复盘.pptx", "复盘-2.pptx", "复盘-3.pptx"]
    # 三份文件都真实存在、互不覆盖（各自字节独立）
    for name in names:
        assert (sandbox.workspace_root(WS) / "demo" / "deck" / name).is_file()


# ---------------------------------------------------------------- ④⑤ 中文字体与产物复开


def test_generated_deck_sets_ea_cs_font_and_reopens():
    """生成 run 的 rPr 必须带 a:ea / a:cs typeface（否则中文回落宋体）；
    产物用 python-pptx 重新打开：页数 = 确认卡内容页、每页文本非空。"""
    raw = io.BytesIO()
    Presentation().save(raw)
    pack = theme_pack.create_pack(workspace_id=WS, owner_id="u1", raw=raw.getvalue(), filename="t.pptx")
    outline = Outline.model_validate(_outline_dict(pages=3)).normalize()
    outline.style.theme_pack_id = pack["id"]
    session = session_store.create_session(workspace_id=WS, owner_id="u1", project="demo")
    payload = generator.generate_deck(session, outline, _project(), WS, "u1")

    target = sandbox.workspace_root(WS) / "demo" / payload["deck"]["path"]
    prs = Presentation(str(target))
    # 口径：sources_policy=off → 无附录页，total_slides = 内容页
    assert payload["total_slides"] == outline.content_page_count() == 3
    assert len(prs.slides._sldIdLst) == payload["total_slides"]

    expected_font = pack["style"]["report"]["fonts"]["body"]
    seen_ea = seen_cs = False
    titles: list[str] = []
    for slide in prs.slides:
        for shape in _deep(slide.shapes):
            if not shape.has_text_frame:
                continue
            if shape == getattr(slide.shapes, "title", None) or shape.name.lower().startswith("title"):
                titles.append(shape.text_frame.text)
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if not run.text.strip():
                        continue
                    rpr = run._r.find(qn("a:rPr"))
                    assert rpr is not None, "生成 run 缺 rPr"
                    ea = rpr.find(qn("a:ea"))
                    cs = rpr.find(qn("a:cs"))
                    if ea is not None:
                        seen_ea = True
                        assert ea.get("typeface") == expected_font
                    if cs is not None:
                        seen_cs = True
    assert seen_ea, "没有任何 run 设置 a:ea —— 中文会回落宋体"
    assert seen_cs, "没有任何 run 设置 a:cs"
    # 每一页都有非空文本（占位符填充没漏页）
    for slide in prs.slides:
        texts = [s.text_frame.text for s in _deep(slide.shapes) if s.has_text_frame]
        assert any(t.strip() for t in texts)


# ---------------------------------------------------------------- ⑥ json 后端兼容


def test_stores_work_on_json_backend(tmp_path, monkeypatch):
    """双后端范式：json 后端下主题包与会话 CRUD 全链路可用。"""
    monkeypatch.setattr(theme_pack, "_use_table", lambda: False)
    monkeypatch.setattr(theme_pack, "_JSON", JsonState(tmp_path / "packs.json"))
    monkeypatch.setattr(session_store, "_use_table", lambda: False)
    monkeypatch.setattr(session_store, "_SESSIONS_JSON", JsonState(tmp_path / "sessions.json"))
    monkeypatch.setattr(session_store, "_MESSAGES_JSON", JsonState(tmp_path / "messages.json"))
    monkeypatch.setattr(session_store, "_GENERATIONS_JSON", JsonState(tmp_path / "gens.json"))

    pack = theme_pack.create_pack(workspace_id="wsj", owner_id="u1", raw=_sample_pptx(), filename="j.pptx")
    assert pack["id"] in {p["id"] for p in theme_pack.list_packs("wsj", "u1")}
    assert theme_pack.get_visible_pack(pack["id"], "wsj") is not None
    assert theme_pack.get_visible_pack(pack["id"], "other") is None
    assert theme_pack.delete_pack(pack["id"]) is True

    session = session_store.create_session(workspace_id="wsj", owner_id="u1", title="json 会话")
    message = session_store.append_message(session["id"], "assistant", "卡", {"outline": {"sections": [{"pages": [1]}]}})
    assert session_store.latest_outline(session["id"]) == (message["id"], {"sections": [{"pages": [1]}]})
    generation = session_store.insert_generation(
        session_id=session["id"], project="p", deck_path="deck/a.pptx",
        total_slides=3, layouts_used=["x"], fidelity={"score": 99},
    )
    assert session_store.list_generations(session["id"])[0]["id"] == generation["id"]
    ok, deleted = session_store.delete_session(session["id"])
    assert ok and deleted == 1
