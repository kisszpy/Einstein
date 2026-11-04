"""文档解析器：把 PDF / Word / 纯文本统一抽成纯文本。

支持的格式与策略：
- .pdf  → pypdf 逐页抽取（扫描件无文本层时返回空，需 OCR 才能解决）
- .docx → python-docx 抽段落 + 表格
- .txt / .md 等文本 → UTF-8 / GBK 兜底解码

旧版二进制 .doc 不支持（需 LibreOffice/Word 转换），提示用户转存 .docx。
"""

from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024  # 50MB 上限

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}


class ParseError(Exception):
    """解析失败（含用户可读的原因）。"""


def _decode_text(raw: bytes, filename: str) -> str:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError(f"{filename}: 无法识别文本编码")


def _parse_pdf(raw: bytes, filename: str) -> str:
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 — pypdf 各类损坏都归为 ParseError
        raise ParseError(f"{filename}: PDF 解析失败（{exc}）") from exc
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"【第 {i + 1} 页】\n{text.strip()}")
    if not pages:
        raise ParseError(
            f"{filename}: 未抽取到文本——可能是扫描件（无文本层），当前版本不支持 OCR"
        )
    return "\n\n".join(pages)


def _parse_docx(raw: bytes, filename: str) -> str:
    import io

    import docx

    try:
        document = docx.Document(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"{filename}: Word 解析失败（{exc}）") from exc
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    # 表格内容按行拼接（RAG 常见知识都在表格里）
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    if not parts:
        raise ParseError(f"{filename}: 文档内容为空")
    return "\n".join(parts)


def parse_document(raw: bytes, filename: str) -> str:
    """按扩展名分发解析，返回纯文本。失败抛 ParseError。"""
    if len(raw) > MAX_BYTES:
        raise ParseError(f"{filename}: 文件超过 50MB 上限")

    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(raw, filename)
    if ext == ".docx":
        return _parse_docx(raw, filename)
    if ext in (".txt", ".md", ".markdown"):
        return _decode_text(raw, filename)
    if ext == ".doc":
        raise ParseError(
            f"{filename}: 旧版 .doc 不支持，请在 Word 中另存为 .docx 后上传"
        )
    raise ParseError(
        f"{filename}: 不支持的格式（支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}）"
    )
