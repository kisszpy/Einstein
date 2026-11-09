"""OCR 与图片摄取。

这一组的重点不是"识别得准不准"（那是模型的事），而是**边界**：
没配 OCR 时要说清缺什么、识别为空时不能入库一份空文档、扫描件别悄悄
当成"解析成功但内容为零"。这些是"能搜到"与"永远搜不到"的分界线。
"""

from __future__ import annotations

import io

import pytest

from app.core.config import get_settings
from app.services import ocr
from app.services import parsers
from app.services.parsers import ParseError, parse_document


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """每个用例都从「什么都没配」开始，避免上一个用例的配置漏过来。"""
    for key in (
        "APP_OCR_PROVIDER", "APP_OCR_MODEL", "APP_OCR_BASE_URL", "APP_OCR_API_KEY",
        "APP_OCR_MAX_SIDE", "APP_OCR_MAX_PAGES",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def vision(monkeypatch):
    """假装配了视觉模型；真正的调用在用例里 mock 掉。"""
    monkeypatch.setenv("APP_OCR_PROVIDER", "auto")
    monkeypatch.setenv("APP_OCR_MODEL", "test-vl")
    get_settings.cache_clear()


def _png(size=(80, 40), color=(255, 255, 255)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _blank_pdf(pages: int = 1) -> bytes:
    """一页空白 PDF：没有文本层，正是扫描件的样子。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- 格式登记


def test_image_extensions_are_accepted():
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"):
        assert ext in parsers.SUPPORTED_EXTENSIONS


def test_svg_is_not_treated_as_image():
    """矢量图的"文字"在标记里，直接读源码比 OCR 准。"""
    assert ".svg" not in parsers.SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------- 可用性判定


def test_provider_is_none_when_nothing_configured():
    # 什么都没配时如实说不可用。装了 pytesseract 的机器上这条自动跳过——
    # 断言「本机没装某依赖」会让测试在别的环境里假失败。
    if ocr.provider() != "none":
        pytest.skip("本机装了 pytesseract，auto 会选中它")
    assert ocr.available() is False
    assert "APP_OCR_MODEL" in ocr.status()["reason"]


def test_provider_off_is_respected(monkeypatch):
    monkeypatch.setenv("APP_OCR_PROVIDER", "off")
    get_settings.cache_clear()
    assert ocr.provider() == "none"
    assert "关闭" in ocr.status()["reason"]


def test_provider_vision_needs_model(monkeypatch):
    monkeypatch.setenv("APP_OCR_PROVIDER", "vision")
    get_settings.cache_clear()
    # 显式要 vision 却没给模型：不能假装可用
    assert ocr.provider() == "none"
    assert "APP_OCR_MODEL" in ocr.status()["reason"]


def test_provider_vision_when_model_set(vision):
    assert ocr.provider() == "vision"
    assert ocr.status()["model"] == "test-vl"


# ---------------------------------------------------------------- 图片


def test_image_without_ocr_fails_with_actionable_hint():
    if ocr.provider() != "none":
        pytest.skip("本机装了 pytesseract")
    with pytest.raises(ParseError) as err:
        parse_document(_png(), "发票截图.png")
    msg = str(err.value)
    assert "OCR" in msg
    assert "APP_OCR_MODEL" in msg
    assert "pytesseract" in msg


def test_image_is_ocrd_and_labelled(monkeypatch, vision):
    monkeypatch.setattr(ocr, "extract", lambda raw, **kw: "合同金额：壹万元整")
    text = parse_document(_png(), "合同.png")
    assert "合同金额：壹万元整" in text
    # 标出来源：用户搜到内容时该知道这是从图里认出来的
    assert "合同.png" in text


def test_blank_image_is_rejected_not_ingested(monkeypatch, vision):
    """识别为空时宁可报错——入库一份空文档等于以后永远搜不到它。"""
    monkeypatch.setattr(ocr, "extract", lambda raw, **kw: "   ")
    with pytest.raises(ParseError) as err:
        parse_document(_png(), "照片.png")
    assert "没有识别到文字" in str(err.value)


def test_oversized_image_is_rejected_with_reason():
    raw = b"x" * (ocr.MAX_IMAGE_BYTES + 1)
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(raw, filename="巨图.png")
    assert "MB" in str(err.value)


def test_empty_image_is_rejected():
    with pytest.raises(ocr.OcrError):
        ocr.extract(b"", filename="空.png")


def test_broken_image_reports_decode_error(vision):
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"not an image at all", filename="坏.png")
    assert "无法解码" in str(err.value)


def test_shrink_limits_long_side(monkeypatch):
    """长边缩到配置值：视觉 token 随像素平方涨，原图发过去只会更慢更贵。"""
    from PIL import Image

    monkeypatch.setenv("APP_OCR_MAX_SIDE", "100")
    get_settings.cache_clear()

    body, mime = ocr._shrink(_png(size=(400, 200)))
    assert mime == "image/png"
    img = Image.open(io.BytesIO(body))
    assert max(img.size) == 100


def test_shrink_keeps_small_image_as_png(monkeypatch):
    monkeypatch.setenv("APP_OCR_MAX_SIDE", "2000")
    get_settings.cache_clear()
    body, mime = ocr._shrink(_png(size=(50, 50)))
    assert mime == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 扫描件 PDF


def test_scanned_pdf_without_ocr_names_the_gap():
    if ocr.provider() != "none":
        pytest.skip("本机装了 pytesseract")
    with pytest.raises(ParseError) as err:
        parse_document(_blank_pdf(), "扫描件.pdf")
    msg = str(err.value)
    assert "扫描件" in msg
    assert "OCR" in msg


def test_scanned_pdf_with_ocr_but_no_renderer(monkeypatch, vision):
    """配了 OCR 却没装 pymupdf：要说清装什么，而不是含糊地失败。"""
    monkeypatch.setattr(ocr, "render_pdf_pages", lambda raw, **kw: (0, []))
    with pytest.raises(ParseError) as err:
        parse_document(_blank_pdf(), "扫描件.pdf")
    assert "pymupdf" in str(err.value)


def test_scanned_pdf_ocrs_each_page(monkeypatch, vision):
    monkeypatch.setattr(ocr, "render_pdf_pages", lambda raw, **kw: (2, [b"p1", b"p2"]))
    monkeypatch.setattr(
        ocr, "extract",
        lambda raw, **kw: "第一页正文" if raw == b"p1" else "第二页正文",
    )
    text = parse_document(_blank_pdf(2), "扫描件.pdf")
    assert "第一页正文" in text and "第二页正文" in text
    assert "【第 1 页 · OCR】" in text
    assert "【第 2 页 · OCR】" in text


def test_scanned_pdf_marks_truncation(monkeypatch, vision):
    """200 页扫描件只做前 20 页：必须明说被截断，否则用户以为全文都进来了。"""
    monkeypatch.setattr(ocr, "render_pdf_pages", lambda raw, **kw: (200, [b"p1"]))
    monkeypatch.setattr(ocr, "extract", lambda raw, **kw: "正文")
    text = parse_document(_blank_pdf(1), "厚扫描件.pdf")
    assert "共 200 页" in text
    assert "只识别了前 1 页" in text


def test_scanned_pdf_with_no_recognized_text_is_rejected(monkeypatch, vision):
    monkeypatch.setattr(ocr, "render_pdf_pages", lambda raw, **kw: (1, [b"p1"]))
    monkeypatch.setattr(ocr, "extract", lambda raw, **kw: "")
    with pytest.raises(ParseError) as err:
        parse_document(_blank_pdf(), "空白扫描.pdf")
    assert "没识别到文字" in str(err.value)


def test_normal_pdf_does_not_call_ocr(monkeypatch, vision):
    """有文本层的 PDF 不该触发 OCR——那是几秒 vs 几毫秒的差别。"""
    def boom(*_a, **_kw):
        raise AssertionError("有文本层时不该调用 OCR")

    monkeypatch.setattr(ocr, "render_pdf_pages", boom)
    monkeypatch.setattr(
        parsers, "_parse_pdf",
        lambda raw, name: "【第 1 页】\n正常文本",
    )
    text = parse_document(_blank_pdf(), "正常.pdf")
    assert "正常文本" in text


# ---------------------------------------------------------------- 对外可见性


def test_capability_endpoint_reports_status():
    """开关要能查：前端据此在选图片时提前提示，而不是等上传后 400。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import sessions, users

    admin = users.get_user_store().list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)

    with TestClient(app) as client:
        res = client.get(
            "/api/kb/ocr", headers={"Authorization": f"Bearer {token}"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["provider"] in ("vision", "tesseract", "none")
    assert isinstance(body["available"], bool)
    if not body["available"]:
        assert body["reason"]  # 不可用必须说明原因
