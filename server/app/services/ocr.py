"""图片与扫描件取字（OCR）。

**为什么要有**：知识库里最常被丢进来的恰恰是最难解析的东西——手机拍的合同、
微信转发的截图、扫描版 PDF。它们没有文本层，`pypdf` 抽出来是空的，用户看到
「未抽取到文本」却不知道该怎么办。这一层把图变成字，后面的切片/嵌入/检索
完全不用改。

**两条路，按优先级**：

1. `vision`：走 OpenAI 兼容的多模态端点（本地 llama.cpp 起 Qwen-VL、或云端）。
   识别质量与「按阅读顺序输出、保留表格结构」这类要求都靠提示词约束，比传统
   OCR 更能处理倾斜、反光、手写。缺一个能看图的模型时它就用不了。
2. `tesseract`：装了 `pytesseract` + 语言包就能离线跑，无需模型。对印刷体够用，
   对手写/复杂版式明显差。

`auto` 的判定顺序是「配了视觉模型就用视觉模型，否则看 pytesseract 在不在」。
两条都没有时**不静默降级为空文本**——宁可报一个能照着做的错，也不要让用户
入库一份空文档然后搜索永远搜不到。

所有失败都收敛成 `OcrError`，消息面向用户（说清缺什么、怎么补）。
"""

from __future__ import annotations

import base64
import io
import mimetypes

from app.core.config import get_settings

__all__ = ["OcrError", "IMAGE_EXTENSIONS", "available", "extract", "status"]

# 这些扩展名走 OCR。刻意不含 .svg：矢量图里是标记语言，直接读源码更准。
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

# 一次识别最多多少字节的图（base64 前）。50MB 的原图直接发过去只会超时。
MAX_IMAGE_BYTES = 20 * 1024 * 1024

_PROMPT = """请把这张图片里的所有文字按阅读顺序转录出来。

要求：
- 只输出转录结果，不要解释、不要总结、不要加"以下是..."之类的话
- 保留原有的段落、标题层级与列表
- 表格转成 Markdown 表格，保持行列对齐
- 看不清的字用 □ 代替，不要凭猜测补全
- 图里确实没有文字时，输出空字符串
"""


class OcrError(Exception):
    """OCR 失败（消息面向用户）。"""


def _mime(filename: str) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    if guessed and guessed.startswith("image/"):
        return guessed
    # 猜不出来（少见扩展名）按 png 发，多数端点按内容而非声明解析
    return "image/png"


def _vision_ready() -> bool:
    settings = get_settings()
    return bool((settings.ocr_model or "").strip())


def _tesseract_ready() -> bool:
    try:
        import pytesseract  # noqa: F401
    except Exception:  # noqa: BLE001 — 没装、或装了但找不到 tesseract 可执行文件
        return False
    return True


def provider() -> str:
    """解析出的实际档位：vision | tesseract | none。

    显式配置优先；配了但依赖不满足时如实返回 none，不假装可用——`available()`
    会把原因带出去。
    """
    settings = get_settings()
    raw = (settings.ocr_provider or "auto").strip().lower()
    if raw in ("off", "none", "0", "false", "no"):
        return "none"
    if raw == "vision":
        return "vision" if _vision_ready() else "none"
    if raw == "tesseract":
        return "tesseract" if _tesseract_ready() else "none"
    if _vision_ready():
        return "vision"
    if _tesseract_ready():
        return "tesseract"
    return "none"


def available() -> bool:
    return provider() != "none"


def status() -> dict:
    """给诊断接口用：现在能不能 OCR、走哪条路、为什么不能用。"""
    settings = get_settings()
    picked = provider()
    reason = ""
    if picked == "none":
        if (settings.ocr_provider or "auto").lower() in ("off", "none"):
            reason = "已通过 APP_OCR_PROVIDER=off 关闭"
        elif (settings.ocr_provider or "").lower() == "vision":
            reason = "APP_OCR_PROVIDER=vision 但 APP_OCR_MODEL 为空"
        elif (settings.ocr_provider or "").lower() == "tesseract":
            reason = "APP_OCR_PROVIDER=tesseract 但未安装 pytesseract"
        else:
            reason = "未配置 APP_OCR_MODEL，且未安装 pytesseract"
    return {
        "provider": picked,
        "available": picked != "none",
        "model": (settings.ocr_model or "") if picked == "vision" else "",
        "reason": reason,
    }


# ---------------------------------------------------------------- 视觉模型


def _shrink(raw: bytes) -> tuple[bytes, str]:
    """把图缩到端点能吃得下的大小；PIL 不在时原样返回。

    视觉模型的视觉 token 随像素平方增长，一张 4000px 的手机照片能顶掉半张
    上下文。缩到长边 `ocr_max_side` 对识别率几乎无损（文字仍远大于字高）。
    """
    settings = get_settings()
    limit = int(settings.ocr_max_side or 0)
    if limit <= 0:
        return raw, _mime("x.png")
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 — 没装 Pillow 就按原图发
        return raw, _mime("x.png")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001 — 不是图 / 截断
        raise OcrError(f"图片无法解码（{type(exc).__name__}），请确认是完整的图片文件") from exc

    if max(img.size) > limit:
        scale = limit / max(img.size)
        img = img.resize(
            (max(int(img.width * scale), 1), max(int(img.height * scale), 1)),
            Image.LANCZOS,
        )
    # 统一成 PNG：原格式可能是 CMYK JPEG / 16 位 TIFF，端点不一定认
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def _extract_vision(raw: bytes, *, filename: str) -> str:
    settings = get_settings()
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise OcrError("缺少 openai 依赖，无法调用视觉模型") from exc

    body, mime = _shrink(raw)
    data_url = f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"

    base_url = (settings.ocr_base_url or settings.llm_base_url or "").strip()
    api_key = (settings.ocr_api_key or settings.llm_api_key or "not-needed").strip()
    client = OpenAI(
        base_url=base_url or None,
        api_key=api_key,
        timeout=float(settings.ocr_timeout or 120),
        max_retries=1,
    )
    try:
        resp = client.chat.completions.create(
            model=settings.ocr_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            # 转录是确定性的活，温度拉高只会多出错字
            temperature=0,
            max_tokens=int(settings.ocr_max_tokens or 4096),
        )
    except Exception as exc:  # noqa: BLE001 — 端点不可用 / 模型不支持图片
        raise OcrError(
            f"{filename}: 视觉模型调用失败（{type(exc).__name__}: {exc}）。"
            "确认 APP_OCR_MODEL 指向的模型支持图片输入"
        ) from exc

    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    return text


# ---------------------------------------------------------------- tesseract


def _extract_tesseract(raw: bytes, *, filename: str) -> str:
    settings = get_settings()
    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise OcrError("未安装 pytesseract/Pillow，无法使用本地 OCR") from exc

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"图片无法解码（{type(exc).__name__}），请确认是完整的图片文件") from exc

    lang = (settings.ocr_lang or "chi_sim+eng").strip()
    try:
        return (pytesseract.image_to_string(img, lang=lang) or "").strip()
    except Exception as exc:  # noqa: BLE001 — 多半是语言包没装
        raise OcrError(
            f"{filename}: 本地 OCR 失败（{exc}）。检查 tesseract 是否安装并带 "
            f"{lang} 语言包，或改用 APP_OCR_MODEL 走视觉模型"
        ) from exc


# ---------------------------------------------------------------- 入口


def extract(raw: bytes, *, filename: str = "image", hint: str = "") -> str:
    """从图片字节里取文字。取不到返回空串（由调用方决定是否当失败）。"""
    if not raw:
        raise OcrError("图片内容为空")
    if len(raw) > MAX_IMAGE_BYTES:
        raise OcrError(
            f"{filename}: 图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB，"
            "请压缩后再上传（手机原图常见 5~10MB，长截图容易超）"
        )

    picked = provider()
    if picked == "vision":
        return _extract_vision(raw, filename=filename)
    if picked == "tesseract":
        return _extract_tesseract(raw, filename=filename)

    why = status()["reason"] or "未启用 OCR"
    raise OcrError(
        f"{filename}: 需要 OCR 才能读这张图，但当前环境不可用（{why}）。"
        "配置方式二选一：设置 APP_OCR_MODEL 指向一个支持图片的 OpenAI 兼容模型，"
        "或安装 pytesseract 与 tesseract 语言包。"
        + (f" {hint}" if hint else "")
    )


def render_pdf_pages(
    raw: bytes, *, max_pages: int = 20, dpi: int = 200,
) -> tuple[int, list[bytes]]:
    """把 PDF 每页渲染成 PNG，供 OCR 用。返回 (总页数, 前 max_pages 页的 PNG)。

    缺少 pymupdf 时返回 (0, [])，由调用方给出「装什么才能用」的提示。

    `max_pages` 是有意的上限：一份 200 页的扫描件逐页 OCR 要几十分钟、几块钱，
    与其让用户以为卡死，不如先做前几页并明确告诉他被截断了。
    """
    try:
        import fitz  # pymupdf
    except Exception:  # noqa: BLE001
        return 0, []

    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"PDF 无法渲染（{type(exc).__name__}）") from exc
    out: list[bytes] = []
    total = 0
    try:
        total = doc.page_count
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            pix = page.get_pixmap(dpi=dpi)
            out.append(pix.tobytes("png"))
    finally:
        doc.close()
    return total, out


def pdf_render_available() -> bool:
    try:
        import fitz  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True
