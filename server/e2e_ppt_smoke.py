"""PPT 全链路冒烟：HTTP 层完整走一遍 主流程（不打真模型/真联网）。

上传模板 → 建会话 → analyze SSE → 保存确认大纲 → generate SSE →
票据重签 → deck 下载。LLM 与联网用进程内替身，其余全走真实路由与存储。
跑法：server 目录下 .venv/Scripts/python.exe e2e_ppt_smoke.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="ppt-e2e-"))
os.environ["APP_STORE_BACKEND"] = "sqlite"
os.environ["APP_SQLITE_PATH"] = str(_TMP / "e2e.db")
os.environ["APP_VECTOR_BACKEND"] = "memory"
os.environ["APP_QUEUE_BACKEND"] = "local"
os.environ["APP_RATELIMIT_ENABLED"] = "false"
os.environ["APP_AGENT_CHECKPOINT"] = "memory"
os.environ["APP_AGENT_APPROVAL_TOOLS"] = ""
os.environ["APP_LOG_LEVEL"] = "WARNING"

from pptx import Presentation  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.main import app  # noqa: E402
from app.services import sessions, users, web_augment  # noqa: E402
from app.services.ppt_studio import outliner, thumbs  # noqa: E402

thumbs._DATA_DIR = _TMP / "thumbs"


def _sample_pptx() -> bytes:
    """现场造一份带主题与母版文本的最小模板。"""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "示例封面"
    if slide.placeholders[1].has_text_frame:
        slide.placeholders[1].text_frame.text = "示例副标题"
    for para in slide.shapes.title.text_frame.paragraphs:
        for run in para.runs:
            run.font.size = Pt(28)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


async def fake_decide(*args, **kwargs):
    return web_augment.Decision(False, "", "off")


async def no_collect(*args, **kwargs):  # pragma: no cover
    raise AssertionError("不应联网")


async def fake_stream(messages, **kwargs):
    yield "delta", json.dumps(
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


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in text.split("\n\n"):
        if not chunk.strip():
            continue
        event, data = "", {}
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((event, data))
    return events


def main() -> int:
    outliner.web_augment.decide = fake_decide
    outliner.web_augment.collect = no_collect
    outliner.stream_chat = fake_stream

    client = TestClient(app)

    # 登录票据：给首个用户签真会话令牌
    store = users.get_user_store()
    admin = store.list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[1] 登录票据 OK（user={admin.username}）")

    # 上传模板
    res = client.post(
        "/api/ppt/templates",
        headers=headers,
        files={"file": ("company.pptx", _sample_pptx(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        data={"name": "公司蓝白模板"},
    )
    assert res.status_code == 200, res.text
    pack = res.json()
    print(f"[2] 模板上传 OK id={pack['id']} layouts={pack['layout_count']} fidelity={pack['fidelity_score']}")
    assert client.get("/api/ppt/templates", headers=headers).status_code == 200

    # 建项目（生成落盘的目标）
    res = client.post("/api/projects", headers=headers, json={"name": "ppt-e2e-demo", "template": "blank"})
    assert res.status_code == 200, res.text
    project_name = res.json()["project"]
    print(f"[3a] 项目创建 OK project={project_name}")

    # 建会话（默认专家 ppt-beautifier）
    res = client.post(
        "/api/ppt/sessions",
        headers=headers,
        json={"title": "季度经营复盘", "expert_id": "ppt-beautifier", "theme_pack_id": pack["id"], "project": project_name},
    )
    assert res.status_code == 200, res.text
    session = res.json()
    sid = session["id"]
    print(f"[3] 会话创建 OK id={sid} expert={session['expert_id']}")

    # analyze SSE
    res = client.post(
        f"/api/ppt/sessions/{sid}/analyze",
        headers=headers,
        json={"prompt": "做一份第三季度经营复盘，面向管理层，8 页左右"},
    )
    assert res.status_code == 200, res.text
    events = parse_sse(res.text)
    names = [e for e, _ in events]
    assert names[0] == "meta" and "outline" in names and names[-1] == "done", names
    assert names.count("phase") >= 3, names
    outline = next(data for e, data in events if e == "outline")["outline"]
    message_id = next(data for e, data in events if e == "outline")["message_id"]
    detail = client.get(f"/api/ppt/sessions/{sid}", headers=headers).json()
    assert detail["session"]["status"] == "awaiting_confirm", detail["session"]
    print(f"[4] analyze OK 事件={names} 内容页={outline['page_count']} status=awaiting_confirm")

    # 确认大纲（保存用户编辑）
    outline["title"] = "季度经营复盘（用户确认版）"
    res = client.put(
        f"/api/ppt/sessions/{sid}/outline",
        headers=headers,
        json={"outline": outline, "message_id": message_id},
    )
    assert res.status_code == 200, res.text
    saved = res.json()
    print(f"[5] 大纲确认 OK page_count={saved['page_count']} message_id={saved['message_id']}")

    # generate SSE
    res = client.post(
        f"/api/ppt/sessions/{sid}/generate",
        headers=headers,
        json={"outline": saved["outline"], "project": project_name},
    )
    assert res.status_code == 200, res.text
    events = parse_sse(res.text)
    names = [e for e, _ in events]
    assert "phase" in names and "result" in names and names[-1] == "done", names
    generation = next(data for e, data in events if e == "result")["generation"]
    deck = generation["deck"]
    print(f"[6] generate OK total_slides={generation['total_slides']} fidelity={generation['fidelity']['score']} 文件={deck['name']}")

    # 票据重签：两次列表 ticket 不同
    g1 = client.get(f"/api/ppt/sessions/{sid}/generations", headers=headers).json()
    g2 = client.get(f"/api/ppt/sessions/{sid}/generations", headers=headers).json()
    assert g1[0]["deck"]["ticket"] != g2[0]["deck"]["ticket"], "ticket 应每次重签"
    print(f"[7] 票据重签 OK ticket1={g1[0]['deck']['ticket'][:12]}… ticket2={g2[0]['deck']['ticket'][:12]}…")

    # deck 下载（票据鉴权）
    dl = client.get(g2[0]["deck"]["download_url"], headers=headers)
    assert dl.status_code == 200 and len(dl.content) > 1000, (dl.status_code, len(dl.content))
    print(f"[8] deck 下载 OK {len(dl.content)} bytes（pptx 字节完整）")

    detail = client.get(f"/api/ppt/sessions/{sid}", headers=headers).json()
    assert detail["session"]["status"] == "done", detail["session"]
    result_msgs = [m for m in detail["messages"] if m["payload"].get("result")]
    assert result_msgs, "result 消息应落库"
    print("[9] 会话终态 done，result 消息落库 OK")

    _builtin_leg(client, headers, project_name, saved["outline"])
    print("\nE2E SMOKE: ALL PASS（14 步全链路走通）")
    return 0


def _builtin_leg(client, headers: dict, project_name: str, base_outline: dict) -> None:
    """内置设计风格腿：设计台目录 → 实时预览 → HTML 实测生成 → 缩略图票据取图。"""
    catalog = client.get("/api/ppt/designs", headers=headers)
    assert catalog.status_code == 200, catalog.text
    designs_payload = catalog.json()
    assert len(designs_payload["families"]) >= 6, designs_payload["families"]
    assert "type_scale" in designs_payload["knobs"]
    print(f"[10] 设计台目录 OK 家族={len(designs_payload['families'])} "
          f"实测引擎={designs_payload['engine']['html']}")

    outline = json.loads(json.dumps(base_outline))
    outline["style"] = {
        "theme_pack_id": "builtin:midnight", "density": "normal",
        "type_scale": 1.06, "radius": 18, "whitespace": 1.1,
        "accent": "#E11D63", "graphic": "gradient", "seed": 42,
    }
    outline["sections"][0]["pages"].insert(1, {
        "title": "各产线设备数", "layout": "chart",
        "bullets": ["一号车间 1240 台", "二号车间 860 台", "三号车间 620 台"],
    })
    res = client.post(
        "/api/ppt/sessions", headers=headers,
        json={"title": "内置风格全链路", "theme_pack_id": "builtin:midnight", "project": project_name},
    )
    assert res.status_code == 200, res.text
    bsid = res.json()["id"]
    res = client.put(f"/api/ppt/sessions/{bsid}/outline", headers=headers, json={"outline": outline})
    assert res.status_code == 200, res.text

    res = client.post(f"/api/ppt/sessions/{bsid}/preview", headers=headers, json={"limit": 4})
    assert res.status_code == 200, res.text
    preview = res.json()
    if preview["available"]:
        assert preview["slides"] and all(s["image"].startswith("data:image/jpeg") for s in preview["slides"])
        assert preview["tokens"]["colors"]["accent"].upper() == "E11D63", preview["tokens"]
        print(f"[11] 实时预览 OK {len(preview['slides'])} 页 "
              f"体检={preview['lint']} 强调色已生效")
    else:
        print(f"[11] 实时预览降级 OK（{preview['reason']}）")

    res = client.post(
        f"/api/ppt/sessions/{bsid}/generate", headers=headers,
        json={"outline": outline, "project": project_name},
    )
    assert res.status_code == 200, res.text
    events = parse_sse(res.text)
    names = [e for e, _ in events]
    assert names[-1] == "done", events[-3:]
    phases = [d.get("phase") for e, d in events if e == "phase"]
    generation = next(d for e, d in events if e == "result")["generation"]
    engine = generation["fidelity"].get("engine")
    print(f"[12] 内置风格生成 OK engine={engine} 页={generation['total_slides']} "
          f"阶段={phases} 保真={generation['fidelity']['score']}")

    listed = client.get(f"/api/ppt/sessions/{bsid}/generations", headers=headers).json()
    assert listed and listed[0]["generation_id"] == generation["generation_id"]
    thumb_count = listed[0]["thumb_count"]
    ticket = listed[0]["deck"]["ticket"]
    if thumb_count:
        # <img> 带不了 Authorization：只给票据也必须能取到图
        img = client.get(f"/api/ppt/thumbs/{generation['generation_id']}/0?ticket={ticket}")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg", img.status_code
        assert img.content[:2] == b"\xff\xd8", img.content[:8]
        denied = client.get(f"/api/ppt/thumbs/{generation['generation_id']}/0")
        assert denied.status_code in (401, 403), denied.status_code
        print(f"[13] 缩略图 OK {thumb_count} 张，票据取图 {len(img.content)} bytes，无票据被拒")
    else:
        assert engine == "shapes", "实测引擎必须产出缩略图"
        print("[13] 缩略图跳过（回落基础排版，无实测截图）")

    blob = client.get(listed[0]["deck"]["download_url"], headers=headers).content
    prs = Presentation(io.BytesIO(blob))
    assert abs(prs.slide_width / prs.slide_height - 16 / 9) < 1e-4
    texts, pictures = 0, 0

    def walk(shapes):
        nonlocal texts, pictures
        for shape in shapes:
            if shape.shape_type == 6:  # GROUP
                walk(shape.shapes)
            elif shape.shape_type == 13:  # PICTURE
                pictures += 1
            elif shape.has_text_frame and shape.text_frame.text.strip():
                texts += 1

    for slide in prs.slides:
        walk(slide.shapes)
    assert pictures == 0, f"成稿不应含图片贴图，实测 {pictures} 张"
    assert texts >= len(prs.slides), texts
    print(f"[14] 成稿原生可编辑 OK {len(prs.slides)} 页 / {texts} 个非空文本框 / 0 张贴图")


if __name__ == "__main__":
    raise SystemExit(main())
