"""slidep DSL 清单与编辑链路（deck.apply_dsl_page）的回归测试。

背景（2026-11-10）：slidep 产物此前没有 sidecar，浏览页编辑器只能
启发式解析（editable=partial），第一次保存就把渐变/玻璃卡洗成模板风。
现在 slide_upsert 逐页登记 DSL 清单，DSL 即真相源，改→lint→重编译无损往返。
测试不依赖 slidep CLI：lint/upsert 用替身注入。
"""

from __future__ import annotations

import os

import pytest
from pptx import Presentation
from pptx.util import Inches

from app.services.capabilities import deck, sandbox


def _make_pptx(path, pages: int = 1) -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    for _ in range(pages):
        prs.slides.add_slide(blank)
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))


class _FakeSlidep:
    """deck.apply_dsl_page 里用到的 slidep 最小替身。"""

    def __init__(self, diagnostics=None):
        self.diagnostics = diagnostics or []
        self.upserts: list[tuple[str, int]] = []

    def lint(self, pptx, dsl, page_index=0):
        has_error = any(
            str(d.get("severity", "")).lower() in {"error", "fatal"} for d in self.diagnostics
        )
        return {"ok": not has_error, "diagnostics": self.diagnostics}

    def upsert(self, pptx, dsl, page_index=-1):
        self.upserts.append((str(pptx), page_index))
        return {"ok": True}


@pytest.fixture
def no_com(monkeypatch):
    """预览图导出依赖本机 PowerPoint，测试里一律跳过。"""
    monkeypatch.setattr(deck, "_slide_images", lambda root, target: [])


@pytest.fixture
def ws_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = sandbox.workspace_root("ws1") / "demo"
    root.mkdir(parents=True)
    return root


def test_dsl_manifest_roundtrip(ws_root, no_com):
    rel = "deck/demo.pptx"
    target = ws_root / rel
    _make_pptx(target, 0)
    dsl_dir = ws_root / "slides"
    dsl_dir.mkdir(parents=True)
    (dsl_dir / "01.slide").write_text("<Slide>1</Slide>", encoding="utf-8")
    (dsl_dir / "02.slide").write_text("<Slide>2</Slide>", encoding="utf-8")
    # 显式页号登记三页（真实链路里 -1 表示追加，页号由 upsert 后的 pptx 页数决定）
    assert deck.remember_dsl_page(ws_root, rel, 0, "slides/01.slide", "") is True
    assert deck.remember_dsl_page(ws_root, rel, 1, "slides/02.slide", "") is True
    assert deck.remember_dsl_page(ws_root, rel, 2, "", "<Slide>3</Slide>") is True
    # 替换第 1 页
    assert deck.remember_dsl_page(ws_root, rel, 1, "", "<Slide>2b</Slide>") is True

    data = deck.load_deck(ws_root, rel)
    assert data["engine"] == "slidep"
    assert data["editable"] == "full"
    assert data["source"] == "sidecar"
    pages = data["pages"]
    assert [p["dsl_file"] for p in pages] == ["slides/01.slide", "", ""]
    assert pages[0]["dsl"] == "<Slide>1</Slide>"  # dsl_file 内容回读
    assert pages[1]["dsl"] == "<Slide>2b</Slide>"  # 替换生效
    assert pages[2]["dsl"] == "<Slide>3</Slide>"  # 内联 DSL 直接返回
    # 前端缩略图按 slides 长度渲染，必须与页数一致
    assert len(data["slides"]) == len(pages)


def test_dsl_manifest_staleness(ws_root, no_com):
    """pptx 比 manifest 新（被外部改过）→ 清单作废，回退启发式解析。"""
    rel = "deck/demo.pptx"
    target = ws_root / rel
    _make_pptx(target, 1)
    assert deck.remember_dsl_page(ws_root, rel, 0, "", "<Slide/>") is True
    stat = target.stat()
    os.utime(target, (stat.st_atime, stat.st_mtime + 10))
    data = deck.load_deck(ws_root, rel)
    assert "engine" not in data
    assert data["source"] == "parsed"


def test_structured_sidecar_wins(ws_root, no_com):
    """ppt_create 的结构化 sidecar 优先；混合生成不覆盖。"""
    rel = "deck/demo.pptx"
    target = ws_root / rel
    _make_pptx(target, 1)
    deck.remember(ws_root, rel, {"title": "T", "slides": [{"layout": "title", "title": "T"}]})
    assert deck.remember_dsl_page(ws_root, rel, 0, "", "<Slide/>") is False
    data = deck.load_deck(ws_root, rel)
    assert data["source"] == "sidecar"
    assert "engine" not in data
    assert data["slides"][0]["title"] == "T"


def test_apply_dsl_page_happy(ws_root, no_com, monkeypatch):
    rel = "deck/demo.pptx"
    target = ws_root / rel
    _make_pptx(target, 1)
    deck.remember_dsl_page(ws_root, rel, 0, "", "<Slide/>")
    fake = _FakeSlidep()
    monkeypatch.setattr(deck, "slidep", fake)

    # 只校验：不动文件
    res = deck.apply_dsl_page(ws_root, rel, 0, "<Slide width={1280} height={720}/>", apply=False)
    assert res["ok"] is True
    assert res["diagnostics"] == []
    assert fake.upserts == []

    # 编译保存：upsert 一页，manifest 刷新，返回最新 deck
    res = deck.apply_dsl_page(ws_root, rel, 0, "<Slide width={1280} height={720}/>", apply=True)
    assert res["ok"] is True and len(fake.upserts) == 1
    assert res["deck"]["engine"] == "slidep"
    assert res["deck"]["pages"][0]["dsl"] == "<Slide width={1280} height={720}/>"


def test_apply_dsl_page_rejects_error(ws_root, no_com, monkeypatch):
    rel = "deck/demo.pptx"
    target = ws_root / rel
    _make_pptx(target, 1)
    deck.remember_dsl_page(ws_root, rel, 0, "", "<Slide/>")
    fake = _FakeSlidep([{"ruleId": "no-overflow", "severity": "error", "message": "溢出 20px"}])
    monkeypatch.setattr(deck, "slidep", fake)

    res = deck.apply_dsl_page(ws_root, rel, 0, "<Slide>bad</Slide>", apply=True)
    assert res["ok"] is False
    assert res["diagnostics"][0]["ruleId"] == "no-overflow"
    assert fake.upserts == []  # 有 error 绝不落盘


def test_apply_dsl_page_rejects_empty_and_unknown(ws_root, no_com, monkeypatch):
    rel = "deck/demo.pptx"
    _make_pptx(ws_root / rel, 1)
    fake = _FakeSlidep()
    monkeypatch.setattr(deck, "slidep", fake)
    with pytest.raises(sandbox.SandboxError):
        deck.apply_dsl_page(ws_root, rel, 0, "   ", apply=True)
    with pytest.raises(sandbox.SandboxError):
        deck.apply_dsl_page(ws_root, "deck/no-such.pptx", 0, "<Slide/>", apply=True)
    assert fake.upserts == []
