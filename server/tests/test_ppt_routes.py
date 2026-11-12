"""PPT 路由层：模板上传/删除、会话 CRUD、analyze/generate SSE 流、票据重签。

analyze/generate 的外部依赖（联网检索、LLM、检索）全部 monkeypatch，
绝不打真模型。SSE 断言按事件名顺序检查。
"""

from __future__ import annotations

import io
import json

import pytest
from pptx import Presentation

from app.services.capabilities import sandbox
from app.services import projects as project_registry
from app.services import web_augment
from app.services.ppt_studio import outliner, session_store, theme_pack


# ---------------------------------------------------------------- 夹具


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
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


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def auth_header():
    """造一个真会话：中间件在路由依赖之前就鉴权，覆盖依赖绕不过去。"""
    from app.services import sessions, users

    store = users.get_user_store()
    admin = store.list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)
    return {"Authorization": f"Bearer {token}"}, admin.id


def _sample_pptx() -> bytes:
    buf = io.BytesIO()
    Presentation().save(buf)
    return buf.getvalue()


def _mock_outline_text() -> str:
    return json.dumps(
        {
            "outline": {
                "title": "季度经营复盘",
                "subtitle": "2026 Q3",
                "sources_policy": "off",
                "sections": [
                    {
                        "title": "正文",
                        "pages": [
                            {"title": "开场", "layout": "cover"},
                            {"title": "核心结论", "layout": "bullets", "bullets": ["营收稳增", "成本可控"]},
                            {"title": "收尾", "layout": "end"},
                        ],
                    }
                ],
            }
        },
        ensure_ascii=False,
    )


@pytest.fixture()
def no_web(monkeypatch):
    """关掉联网：decide 直接判 off，collect 不应被调用（两者都是 async）。"""

    async def fake_decide(*args, **kwargs):
        return web_augment.Decision(False, "", "off")

    async def no_collect(*args, **kwargs):
        raise AssertionError("不应联网")

    monkeypatch.setattr(outliner.web_augment, "decide", fake_decide)
    monkeypatch.setattr(outliner.web_augment, "collect", no_collect)


@pytest.fixture()
def mock_llm(monkeypatch):
    """LLM 恒定输出一份合法 outline JSON。"""

    async def fake_stream(messages, **kwargs):
        yield "delta", _mock_outline_text()

    monkeypatch.setattr(outliner, "stream_chat", fake_stream)


# ---------------------------------------------------------------- 预置专家


def test_ppt_beautifier_preset_with_stable_id():
    from app.services.experts import get_expert_store

    expert = get_expert_store().get("ppt-beautifier")
    assert expert is not None
    assert expert.name == "PPT 美化专家"
    assert expert.status == "live"


# ---------------------------------------------------------------- 模板路由


def test_template_upload_list_detail_delete(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        res = client.post(
            "/api/ppt/templates",
            headers=headers,
            files={"file": ("公司模板.pptx", _sample_pptx(), "application/octet-stream")},
        )
        assert res.status_code == 200, res.text
        pack = res.json()
        assert pack["name"] == "公司模板"
        assert pack["layout_count"] >= 5

        rows = client.get("/api/ppt/templates", headers=headers).json()
        assert pack["id"] in {r["id"] for r in rows}

        detail = client.get(f"/api/ppt/templates/{pack['id']}", headers=headers).json()
        assert detail["layouts"] and detail["palette"]["accent1"]

        assert client.delete(f"/api/ppt/templates/{pack['id']}", headers=headers).json()["ok"]
        assert client.get(f"/api/ppt/templates/{pack['id']}", headers=headers).status_code == 404


def test_designs_catalog_lists_the_uploaded_template_as_a_family(auth_header):
    """上传模板也是一支设计家族，排最前——设计台对模板同样能拧旋钮。"""
    headers, _uid = auth_header
    with _client() as client:
        res = client.post(
            "/api/ppt/templates",
            headers=headers,
            files={"file": ("公司模板.pptx", _sample_pptx(), "application/octet-stream")},
        )
        assert res.status_code == 200, res.text
        pack = res.json()
        catalog = client.get("/api/ppt/designs", headers=headers).json()

    families = catalog["families"]
    assert families[0]["id"] == pack["id"]
    assert families[0]["kind"] == "template"
    assert families[0]["ratio"] == "4:3"  # python-pptx 内置模板是 10×7.5in
    assert len(families[0]["swatches"]) == 4
    assert len(families[0]["chart"]) == 7
    assert families[0]["fonts"]["body"]
    assert [f["kind"] for f in families[1:]] == ["builtin"] * 6


def test_template_rejects_non_pptx(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        res = client.post(
            "/api/ppt/templates",
            headers=headers,
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
    assert res.status_code == 400
    assert "template_invalid_suffix" in res.text


def test_template_rejects_corrupted_pptx(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        res = client.post(
            "/api/ppt/templates",
            headers=headers,
            files={"file": ("broken.pptx", b"PK\x03\x04garbage", "application/octet-stream")},
        )
    assert res.status_code == 400
    assert "template_invalid" in res.text


# ---------------------------------------------------------------- 会话路由


def test_session_crud_and_ownership(auth_header):
    headers, uid = auth_header
    with _client() as client:
        created = client.post(
            "/api/ppt/sessions", headers=headers,
            json={"title": "经营复盘", "expert_id": "ppt-beautifier"},
        ).json()
        assert created["status"] == "idle"
        sid = created["id"]

        detail = client.get(f"/api/ppt/sessions/{sid}", headers=headers).json()
        assert detail["session"]["title"] == "经营复盘"
        assert detail["messages"] == []

        rows = client.get("/api/ppt/sessions", headers=headers).json()
        assert sid in {r["id"] for r in rows}

        # 别人的会话当不存在（404，不泄露存在性）
        from app.services import users as users_service

        other = users_service.get_user_store().list_all()[-1]
        if other.id != uid:
            from app.services import sessions as sessions_service

            token, _ = sessions_service.get_session_store().issue(other.id)
            foreign = client.get(f"/api/ppt/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
            assert foreign.status_code == 404

        deleted = client.delete(f"/api/ppt/sessions/{sid}", headers=headers).json()
        assert deleted["ok"] is True
        assert client.get(f"/api/ppt/sessions/{sid}", headers=headers).status_code == 404


def test_session_rejects_unknown_project_and_expert(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        res = client.post("/api/ppt/sessions", headers=headers, json={"project": "ghost"})
        assert res.status_code == 400
        assert "project_not_found" in res.text
        res = client.post("/api/ppt/sessions", headers=headers, json={"expert_id": "nobody"})
        assert res.status_code == 400


# ---------------------------------------------------------------- analyze SSE


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        event = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if event and data:
            events.append((event, json.loads(data)))
    return events


def test_analyze_streams_outline_end_to_end(auth_header, no_web, mock_llm):
    headers, _uid = auth_header
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={}).json()["id"]
        res = client.post(f"/api/ppt/sessions/{sid}/analyze", headers=headers, json={"prompt": "做一份季度复盘 PPT"})
        assert res.status_code == 200
        events = _sse_events(res.text)

    names = [e for e, _ in events]
    assert names[0] == "meta"
    assert names.count("phase") >= 3  # collecting / kb / outlining / ready
    assert "sources" in names
    assert "delta" in names
    assert names[-1] == "done"
    outline_event = next(d for e, d in events if e == "outline")
    assert outline_event["outline"]["title"] == "季度经营复盘"
    assert outline_event["outline"]["sections"][0]["pages"][0]["id"]

    # 落库：assistant 消息带 outline payload，会话进入 awaiting_confirm
    detail = client.get(f"/api/ppt/sessions/{sid}", headers=headers).json()
    assert detail["session"]["status"] == "awaiting_confirm"
    payloads = [m["payload"] for m in detail["messages"]]
    assert any("outline" in p for p in payloads)
    # 页数口径落在会话上
    assert detail["session"]["page_count"] == 3


def test_analyze_clarification_once(auth_header, no_web, monkeypatch):
    headers, _uid = auth_header

    async def fake_stream(messages, **kwargs):
        yield "delta", json.dumps({"clarification": {"question": "受众是谁？", "options": ["管理层", "全员"]}}, ensure_ascii=False)

    monkeypatch.setattr(outliner, "stream_chat", fake_stream)
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={}).json()["id"]
        res = client.post(f"/api/ppt/sessions/{sid}/analyze", headers=headers, json={"prompt": "做个 PPT"})
        events = _sse_events(res.text)
        assert "clarification" in [e for e, _ in events]
        assert [e for e, _ in events][-1] == "done"
        detail = client.get(f"/api/ppt/sessions/{sid}", headers=headers).json()
        assert detail["session"]["clarify_count"] == 1


def test_analyze_rejects_empty_prompt(auth_header, no_web):
    headers, _uid = auth_header
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={}).json()["id"]
        res = client.post(f"/api/ppt/sessions/{sid}/analyze", headers=headers, json={"prompt": "   "})
    assert res.status_code == 400


# ---------------------------------------------------------------- outline 保存


def test_save_outline_roundtrip_and_validation(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={}).json()["id"]
        ok = client.put(
            f"/api/ppt/sessions/{sid}/outline", headers=headers,
            json={"outline": json.loads(_mock_outline_text())["outline"]},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["page_count"] == 3
        assert body["total_slides"] == body["page_count"]  # 无来源 → 无附录页
        assert body["warnings"] == []

        bad = client.put(
            f"/api/ppt/sessions/{sid}/outline", headers=headers,
            json={"outline": {"title": "", "sections": []}},
        )
        assert bad.status_code == 400
        assert "title_required" in bad.text


# ---------------------------------------------------------------- generate


def _make_project(uid: str) -> str:
    return project_registry.create_project("default", "复盘演示", owner_id=uid)["project"]


def test_generate_requires_project(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={}).json()["id"]
        res = client.post(f"/api/ppt/sessions/{sid}/generate", headers=headers, json={})
    assert res.status_code == 400
    assert "project_required" in res.text


def test_generate_streams_result_and_saves_deck(auth_header, no_web, mock_llm):
    headers, uid = auth_header
    project = _make_project(uid)
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={"project": project}).json()["id"]
        # 先出卡（outline 落到会话），generate 省略 outline 时用最近一份
        client.post(
            f"/api/ppt/sessions/{sid}/analyze", headers=headers,
            json={"prompt": "做一份季度复盘 PPT"},
        )
        res = client.post(f"/api/ppt/sessions/{sid}/generate", headers=headers, json={})
        assert res.status_code == 200, res.text
        events = _sse_events(res.text)

    names = [e for e, _ in events]
    assert names[0] == "meta"
    assert "phase" in names
    assert names[-1] == "done"
    result = next(d for e, d in events if e == "result")["generation"]
    assert result["deck"]["path"].startswith("deck/")
    assert result["deck"]["ticket"]
    assert "ticket=" in result["deck"]["download_url"]
    assert result["total_slides"] == 3

    target = sandbox.workspace_root("default") / project / result["deck"]["path"]
    assert target.is_file()
    prs = Presentation(str(target))
    assert len(prs.slides._sldIdLst) == 3

    detail = client.get(f"/api/ppt/sessions/{sid}", headers=headers).json()
    assert detail["session"]["status"] == "done"
    assert any("result" in m["payload"] for m in detail["messages"])


def test_generations_reissues_ticket_each_call(auth_header, no_web, mock_llm):
    headers, uid = auth_header
    project = _make_project(uid)
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={"project": project}).json()["id"]
        client.post(f"/api/ppt/sessions/{sid}/analyze", headers=headers, json={"prompt": "做一份 PPT"})
        client.post(f"/api/ppt/sessions/{sid}/generate", headers=headers, json={})

        first = client.get(f"/api/ppt/sessions/{sid}/generations", headers=headers).json()
        second = client.get(f"/api/ppt/sessions/{sid}/generations", headers=headers).json()

    assert len(first) == 1
    deck_a, deck_b = first[0]["deck"], second[0]["deck"]
    assert deck_a["ticket"] and deck_b["ticket"]
    assert deck_a["ticket"] != deck_b["ticket"]  # 每次调用重签（15 分钟过期的兜底）


def test_generate_missing_project_returns_404(auth_header, no_web):
    headers, _uid = auth_header
    with _client() as client:
        sid = client.post("/api/ppt/sessions", headers=headers, json={}).json()["id"]
        outline = json.loads(_mock_outline_text())["outline"]
        res = client.post(
            f"/api/ppt/sessions/{sid}/generate", headers=headers,
            json={"outline": outline, "project": "ghost"},
        )
    assert res.status_code == 404
    assert "project_not_found" in res.text
