"""主题包：模板解析（主题色/字体/版式/保真度）与 workspace 级共享 store。

解析链路都是「读真实 pptx 字节」，这里用 python-pptx 现场造样例，
不依赖仓库里的任何二进制。
"""

from __future__ import annotations

import io

import pytest
from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn

from app.services.ppt_studio import theme_pack

_SMARTART_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _sample_pptx(with_smartart: bool = False) -> bytes:
    """造一个最小 .pptx：1 页「标题和内容」，可选注入 SmartArt graphicData。"""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    if with_smartart:
        # 保真度扫描按 slide XML 里的 uri 字符串识别；python-pptx 造不出真
        # SmartArt，注入一个带 diagram uri 的 a:graphicData 即可命中扫描。
        graphic = etree.SubElement(slide._element, f"{{{_A_NS}}}graphicData")
        graphic.set("uri", _SMARTART_URI)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean():
    theme_pack._table().delete_where("1=1")
    yield
    theme_pack._table().delete_where("1=1")


# ---------------------------------------------------------------- 解析


def test_parse_extracts_theme_layouts_and_fidelity():
    parsed = theme_pack.parse_theme_pack(_sample_pptx(), "公司模板.pptx")
    # 主题色：accent 系必须齐且是 6 位 hex（默认 Office 主题有真实色值）
    palette = parsed["palette"]
    assert len(palette["accent1"]) == 6
    for key in ("accent1", "accent2", "accent3", "accent4", "accent5", "accent6"):
        assert palette[key]
    # 字体：ea 缺失时回落微软雅黑（中文不吃宋体兜底的前提）
    fonts = parsed["fonts"]
    assert fonts["minor_ea"]
    assert fonts["major_ea"]
    # 版式：默认模板 11 个版式，第一个是标题页（cover）
    layouts = parsed["layouts"]
    assert len(layouts) >= 5
    assert layouts[0]["has_title"] is True
    assert any(lay["has_body"] for lay in layouts)
    # 保真度：干净样例不该出现 SmartArt/图表降级
    assert parsed["fidelity"]["score"] >= 60
    assert not any(i["kind"] == "smartart" for i in parsed["fidelity"]["issues"])
    assert parsed["status"] in ("ready", "partial")


def test_parse_detects_smartart_and_degrades():
    parsed = theme_pack.parse_theme_pack(_sample_pptx(with_smartart=True), "带图.pptx")
    kinds = {issue["kind"] for issue in parsed["fidelity"]["issues"]}
    assert "smartart" in kinds
    assert any("SmartArt" in d for d in parsed["fidelity"]["degradations"])


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        theme_pack.parse_theme_pack(b"not a zip at all", "broken.pptx")
    with pytest.raises(ValueError):
        theme_pack.parse_theme_pack(b"", "empty.pptx")


# ---------------------------------------------------------------- store


def test_workspace_scoped_visibility():
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=_sample_pptx(), filename="a.pptx")
    # 同空间其他成员可见（workspace 级共享）
    assert pack["id"] in {p["id"] for p in theme_pack.list_packs("ws1", "u2")}
    # 跨空间一律不可见，get_visible_pack 当不存在（不泄露存在性）
    assert theme_pack.list_packs("ws2", "u1") == []
    assert theme_pack.get_visible_pack(pack["id"], "ws2") is None
    assert theme_pack.get_visible_pack(pack["id"], "ws1") is not None


def test_create_pack_keeps_source_and_source_bytes():
    raw = _sample_pptx()
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=raw, filename="模板 A.pptx")
    # 名字取自文件名（去后缀）
    assert pack["name"] == "模板 A"
    # 源文件长期保留（重新生成的基底）
    assert theme_pack.source_bytes(pack) == raw


def test_delete_pack_removes_record_and_source():
    raw = _sample_pptx()
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=raw, filename="a.pptx")
    path = theme_pack._source_abs_path(pack["source_path"])
    assert path.is_file()
    assert theme_pack.delete_pack(pack["id"]) is True
    assert theme_pack.get_pack(pack["id"]) is None
    assert not path.is_file()
    assert theme_pack.delete_pack(pack["id"]) is False  # 幂等


def test_can_edit_owner_or_admin_only():
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=_sample_pptx(), filename="a.pptx")
    assert theme_pack.can_edit(pack, "u1") is True
    assert theme_pack.can_edit(pack, "u2") is False
    assert theme_pack.can_edit(pack, "u2", is_admin=True) is True


def test_list_payload_and_detail_payload_shapes():
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=_sample_pptx(), filename="a.pptx")
    summary = theme_pack.list_payload(pack)
    assert set(summary) == {
        "id", "kind", "name", "tagline", "owner_id", "status", "layout_count",
        "fidelity_score", "swatches", "mode", "ratio", "created_at",
    }
    assert summary["kind"] == "upload"
    assert summary["tagline"] == pack["style"]["report"]["summary_zh"]
    # 色板缩略改用抽出来的底色/主色/副色三格，比五个主题 accent 更接近模板观感
    assert len(summary["swatches"]) == 3
    assert summary["mode"] in ("dark", "light")
    assert summary["ratio"]
    detail = theme_pack.detail_payload(pack)
    assert detail["layouts"]
    assert detail["palette"]["accent1"]
    assert "fidelity" in detail
    assert detail["style"]["accent"] == summary["swatches"][1]
    # python-pptx 内置模板是 4:3，成稿画布必须跟着模板走
    assert detail["style"]["canvas"] == [960, 720]
    assert detail["style"]["ratio"] == "4:3"


def test_style_survives_the_store_round_trip():
    """抽取结论必须真的落库：只测内存里那份 dict 会漏掉「表里没这列」。

    列缺失时写库按列规范静默过滤，列表接口退回主题 accent——多数公司模板压根
    没改主题，用户看到的色板就成了 Office 出厂的 4F81BD，mode/ratio 还全空。
    """
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=_sample_pptx(), filename="a.pptx")
    reread = theme_pack.get_pack(pack["id"])
    assert reread is not None
    assert reread["style"]["report"]["version"] == pack["style"]["report"]["version"]
    assert theme_pack.list_payload(reread) == theme_pack.list_payload(pack)
    assert theme_pack.detail_payload(reread)["style"] == pack["style"]["report"]
    # 补抽路径（style_of → _persist_style）同样要留得住
    theme_pack._persist_style(pack["id"], {"preset": {"x": 1}, "report": {"version": 99}})
    assert theme_pack.get_pack(pack["id"])["style"]["report"]["version"] == 99


def test_legacy_pack_without_style_heals_on_read():
    """023 之前入库的行 style 是空的：列表/详情要就地补抽，不能露出 Office 主题色。"""
    pack = theme_pack.create_pack(workspace_id="ws1", owner_id="u1", raw=_sample_pptx(), filename="a.pptx")
    expected = theme_pack.list_payload(pack)
    theme_pack._persist_style(pack["id"], {})
    legacy = theme_pack.get_pack(pack["id"])
    assert not legacy["style"]
    assert theme_pack.list_payload(legacy) == expected
    assert theme_pack.detail_payload(theme_pack.get_pack(pack["id"]))["style"]["ratio"] == "4:3"
