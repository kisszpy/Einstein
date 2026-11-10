# -*- coding: utf-8 -*-
"""svgdeck（ppt-master SVG→原生 pptx 管线）回归测试。

compile_deck 端到端用例依赖仓库内 vendored 的 ppt-master 转换器
（tools/ppt-master）与 skia-pathops/uharfbuzz 依赖；缺失时该用例跳过。
"""

import pytest

from app.services.capabilities import deck, sandbox, svgdeck

PAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect x="80" y="100" width="400" height="80" rx="12" fill="#151F38"/>
  <text x="100" y="150" font-family="sans-serif" font-size="32" fill="#FFFFFF">标题文本</text>
</svg>
"""

MASTER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="mbg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0B1223"/>
      <stop offset="100%" stop-color="#1B2A4A"/>
    </linearGradient>
  </defs>
  <rect width="1280" height="720" fill="url(#mbg)"/>
  <rect x="60" y="690" width="60" height="4" fill="#6366F1" id="footmark"/>
</svg>
"""


@pytest.fixture
def ws_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = tmp_path / "ws1"
    (root / "demo").mkdir(parents=True)
    return root


def _converter_available() -> bool:
    try:
        svgdeck.converter_dir()
        import pathops  # noqa: F401
        import uharfbuzz  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def test_compose_master_prefixes_ids_and_layers():
    composed = svgdeck.compose_master(PAGE_SVG, MASTER_SVG)
    # 母板 id 加前缀，页面自身不被改名
    assert "mm_mbg" in composed
    assert 'id="mbg"' not in composed
    # 母板 url 引用被重写
    assert "url(#mm_mbg)" in composed and "url(#mbg)" not in composed
    # 母板组在最前（垫底），页面元素仍在
    assert "data-layer=\"master\"" in composed
    assert "标题文本" in composed


def test_compose_master_dangling_gradient_ref():
    # 母板引用不存在的 id 也不能炸
    master = MASTER_SVG.replace("url(#mbg)", "url(#nope)")
    composed = svgdeck.compose_master(PAGE_SVG, master)
    assert "url(#mm_nope)" not in composed  # 不在 rename 表里的 id 保持原样
    assert "url(#nope)" in composed


def test_write_page_numbering_and_size_guard(ws_root):
    rel1 = svgdeck.write_page(ws_root, -1, PAGE_SVG)
    rel2 = svgdeck.write_page(ws_root, -1, PAGE_SVG)
    assert (rel1, rel2) == ("slides/01.svg", "slides/02.svg")
    # 显式页号覆盖
    assert svgdeck.write_page(ws_root, 5, PAGE_SVG) == "slides/05.svg"
    # 画布校验走 viewBox（优先级高于 width/height 属性）
    with pytest.raises(sandbox.SandboxError):
        svgdeck.write_page(ws_root, -1, PAGE_SVG.replace("1280 720", "1920 720"))
    with pytest.raises(sandbox.SandboxError):
        svgdeck.write_page(ws_root, -1, "<html/>")


def test_lint_svg_diagnostics():
    bad = PAGE_SVG.replace('width="1280"', 'width="1024"').replace("1280 720", "1024 720")
    res = svgdeck.lint_svg(bad)
    assert res["ok"] is False
    assert any(d["ruleId"] == "canvas-size" for d in res["diagnostics"])
    ok = svgdeck.lint_svg(PAGE_SVG)
    assert ok["ok"] is True
    empty = svgdeck.lint_svg(PAGE_SVG.replace("<text", "<!-- <text").replace("文本</text>", "文本</text> -->"))
    assert any(d["ruleId"] == "no-text" for d in empty["diagnostics"])


def test_set_dsl_manifest_respects_structured_sidecar(ws_root):
    rel = "deck/demo.pptx"
    target = ws_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    deck.remember(ws_root, rel, {"title": "t", "slides": [{"layout": "cover"}]})
    # 结构化 sidecar 已占用：set_dsl_manifest 不应覆盖
    deck.set_dsl_manifest(ws_root, rel, "svgdeck", ["slides/01.svg"])
    import json
    data = json.loads(deck.sidecar_path(ws_root, rel).read_text("utf-8"))
    assert data.get("slides"), "结构化清单必须保留"
    assert data.get("_engine") != "svgdeck"


@pytest.mark.skipif(not _converter_available(), reason="ppt-master 转换器或其依赖缺失")
def test_compile_deck_end_to_end(ws_root):
    # 预览图导出走 COM，已被 conftest 的 _no_office_com 关掉（本机 WPS 会崩进程）
    rel = "deck/demo.pptx"
    svgdeck.write_page(ws_root, 1, PAGE_SVG)
    svgdeck.write_page(ws_root, 2, PAGE_SVG.replace("标题文本", "第二页"))
    result = svgdeck.compile_deck(ws_root, rel)
    assert result["ok"] is True and result["pages"] == 2 and result["master"] is False

    # 产物是原生形状且文本可编辑
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(ws_root / rel))
    assert len(prs.slides) == 2
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            assert shape.shape_type != MSO_SHAPE_TYPE.PICTURE  # 不能整页贴图
            if shape.has_text_frame:
                texts.append(shape.text_frame.text.strip())
    assert any("标题文本" in t for t in texts)
    assert any("第二页" in t for t in texts)

    # 清单登记为 svgdeck，load_deck 报告对应引擎与可编辑
    data = deck.load_deck(ws_root, rel)
    assert data["engine"] == "svgdeck"
    assert data["editable"] == "full"
    assert len(data["pages"]) == 2


@pytest.mark.skipif(not _converter_available(), reason="ppt-master 转换器或其依赖缺失")
def test_compile_deck_with_master(ws_root):
    rel = "deck/m.pptx"
    svgdeck.write_page(ws_root, 1, PAGE_SVG)
    (ws_root / "slides" / svgdeck.MASTER_NAME).write_text(MASTER_SVG, encoding="utf-8")
    result = svgdeck.compile_deck(ws_root, rel)
    assert result["master"] is True

    from pptx import Presentation
    prs = Presentation(str(ws_root / rel))
    # 母板渐变被编译进页面形状（gradFill 出现在 slide XML）
    slide_xml = prs.slides[0]._element.xml
    assert "gradFill" in slide_xml


# ---------------------------------------------------------------- 编译缓存
#
# 为什么需要它：编辑器里「切页」「重复点保存」都会触发 compile，而整份编译
# 在热进程里也要 0.25s。用内容指纹判断「和上次一模一样」就直接复用。
# 反过来说，指纹漏判会导致**改了内容却看不到变化**，所以失效路径要逐条验。


def test_fingerprint_ignores_mtime_but_tracks_content(ws_root):
    svgdeck.write_page(ws_root, 1, PAGE_SVG)
    pages = [ws_root / "slides" / "01.svg"]
    before = svgdeck._fingerprint(pages, "")

    # 内容不变、只 touch 一下（编辑器保存同一份内容就是这样）→ 指纹不变
    pages[0].touch()
    assert svgdeck._fingerprint(pages, "") == before

    # 内容变了 → 指纹必须变
    pages[0].write_text(PAGE_SVG.replace("标题文本", "改过的标题"), encoding="utf-8")
    assert svgdeck._fingerprint(pages, "") != before


def test_fingerprint_tracks_master_and_page_order(ws_root):
    svgdeck.write_page(ws_root, 1, PAGE_SVG)
    svgdeck.write_page(ws_root, 2, PAGE_SVG.replace("标题文本", "第二页"))
    pages = sorted((ws_root / "slides").glob("[0-9][0-9].svg"))

    no_master = svgdeck._fingerprint(pages, "")
    with_master = svgdeck._fingerprint(pages, MASTER_SVG)
    assert with_master != no_master, "换母板必须重编译"

    assert svgdeck._fingerprint(list(reversed(pages)), "") != no_master, "页序变了必须重编译"


@pytest.mark.skipif(not _converter_available(), reason="ppt-master 转换器或其依赖缺失")
def test_compile_reuses_when_unchanged_and_rebuilds_on_edit(ws_root):
    import json

    svgdeck.write_page(ws_root, 1, PAGE_SVG)
    first = svgdeck.compile_deck(ws_root, "deck/cached.pptx")
    assert first.get("cached") is not True

    manifest = json.loads(deck.sidecar_path(ws_root, "deck/cached.pptx").read_text("utf-8"))
    assert manifest["_engine"] == "svgdeck"
    assert manifest["_source_hash"], "编译后要落下内容指纹"

    # 再编译一次：内容没动，应命中缓存（不重写文件）
    pptx = ws_root / "deck" / "cached.pptx"
    mtime_before = pptx.stat().st_mtime_ns
    second = svgdeck.compile_deck(ws_root, "deck/cached.pptx")
    assert second.get("cached") is True
    assert pptx.stat().st_mtime_ns == mtime_before, "命中缓存时不该重写 pptx"

    # 改一页：缓存必须失效并真的重编译
    svgdeck.write_page(ws_root, 1, PAGE_SVG.replace("标题文本", "新标题"))
    third = svgdeck.compile_deck(ws_root, "deck/cached.pptx")
    assert third.get("cached") is not True
    assert pptx.stat().st_mtime_ns != mtime_before


@pytest.mark.skipif(not _converter_available(), reason="ppt-master 转换器或其依赖缺失")
def test_adding_master_invalidates_cache(ws_root):
    svgdeck.write_page(ws_root, 1, PAGE_SVG)
    svgdeck.compile_deck(ws_root, "deck/m2.pptx")
    assert svgdeck.compile_deck(ws_root, "deck/m2.pptx").get("cached") is True

    # 后补母板：内容页没变，但整份观感变了，必须重编译
    (ws_root / "slides" / svgdeck.MASTER_NAME).write_text(MASTER_SVG, encoding="utf-8")
    rebuilt = svgdeck.compile_deck(ws_root, "deck/m2.pptx")
    assert rebuilt.get("cached") is not True
    assert rebuilt["master"] is True


def test_warm_up_is_silent_when_converter_missing(monkeypatch):
    """预热是纯优化：转换器不在（换机部署）时只能安静跳过，不能影响启动。"""
    from app.services.capabilities import sandbox as sb

    def _boom() -> None:
        raise sb.SandboxError("no converter")

    monkeypatch.setattr(svgdeck, "converter_dir", _boom)
    svgdeck.warm_up()  # 不抛即通过
