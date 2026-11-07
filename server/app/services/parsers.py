"""文档解析器：把各种格式统一抽成带结构的纯文本。

支持：
- .pdf   → pypdf 逐页抽取（扫描件无文本层时提示需 OCR）
- .docx  → python-docx 抽段落 + 表格
- .xlsx  → openpyxl 每个 sheet 转 markdown 表格
- .csv   → 转 markdown 表格
- .pptx  → python-pptx 逐页抽文本框与表格
- .html  → bs4 去脚本/样式/导航后取正文
- .txt/.md 等文本 → UTF-8 / GBK 兜底解码
- URL    → 抓取网页后按 html 处理

解析完统一过一遍 `clean_text`：去控制字符、合并空行、按频次剔除页眉页脚。
旧版 .doc 不支持（需另存为 .docx）。
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Protocol

MAX_BYTES = 50 * 1024 * 1024  # 50MB 上限
MAX_TABLE_ROWS = 5000          # 单表最多抽这么多行，避免超大表吃满内存
MAX_URL_BYTES = 8 * 1024 * 1024

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".html", ".htm",
    ".txt", ".md", ".markdown",
}

_EXT_LABEL = "PDF / Word / Excel / PPT / CSV / HTML / Markdown / 纯文本"


class ParseError(Exception):
    """解析失败（含用户可读的原因）。"""


class OcrProvider(Protocol):
    """OCR 预留接口：默认不启用，接入 PaddleOCR/Tesseract 时实现它即可。"""

    def extract(self, image: bytes) -> str: ...


_ocr: OcrProvider | None = None


def set_ocr(provider: OcrProvider | None) -> None:
    global _ocr
    _ocr = provider


def get_ocr() -> OcrProvider | None:
    return _ocr


# ---------------------------------------------------------------- 清洗


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """去控制字符、合并空行、剔除重复页眉页脚。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    lines = [ln.rstrip() for ln in text.split("\n")]

    # 页眉页脚：短行且反复出现（>=4 次）基本就是模板文字
    counts: dict[str, int] = {}
    for ln in lines:
        key = ln.strip()
        if 0 < len(key) <= 40:
            counts[key] = counts.get(key, 0) + 1
    repeated = {k for k, n in counts.items() if n >= 4}

    kept: list[str] = []
    for ln in lines:
        key = ln.strip()
        if key in repeated and len(kept) > 0:
            continue
        kept.append(ln)
    return _BLANK.sub("\n\n", "\n".join(kept)).strip()


# ---------------------------------------------------------------- 各格式


def _decode_text(raw: bytes, filename: str) -> str:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError(f"{filename}: 无法识别文本编码")


def _parse_pdf(raw: bytes, filename: str) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 — pypdf 各类损坏都归为 ParseError
        raise ParseError(f"{filename}: PDF 解析失败（{exc}）") from exc
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — 单页坏了不该毁掉整份文档
            text = ""
        if text.strip():
            pages.append(f"【第 {i + 1} 页】\n{text.strip()}")
    if not pages:
        hint = "" if get_ocr() is None else "（已启用 OCR 但未取到文本）"
        raise ParseError(
            f"{filename}: 未抽取到文本——可能是扫描件（无文本层）{hint}，"
            "请先做 OCR 或上传可复制文本的版本"
        )
    return "\n\n".join(pages)


def _parse_docx(raw: bytes, filename: str) -> str:
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


def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    width = max(len(r) for r in rows)
    header = header + [""] * (width - len(header))
    out = ["| " + " | ".join(c.replace("|", "\\|") for c in header) + " |"]
    out.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in rows[1:MAX_TABLE_ROWS]:
        row = row + [""] * (width - len(row))
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
    if len(rows) - 1 > MAX_TABLE_ROWS:
        out.append(f"\n（表格过长，仅保留前 {MAX_TABLE_ROWS} 行）")
    return "\n".join(out)


def _parse_xlsx(raw: bytes, filename: str) -> str:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"{filename}: Excel 解析失败（{exc}）") from exc

    parts: list[str] = []
    try:
        for sheet in book.worksheets:
            rows: list[list[str]] = []
            for values in sheet.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v).strip() for v in values])
                if len(rows) > MAX_TABLE_ROWS + 1:
                    break
            body = [r for r in rows if any(c for c in r)]
            if not body:
                continue
            parts.append(f"## {sheet.title}\n{_rows_to_markdown(body)}")
    finally:
        book.close()

    if not parts:
        raise ParseError(f"{filename}: 工作簿里没有可用内容")
    return "\n\n".join(parts)


def _parse_csv(raw: bytes, filename: str) -> str:
    text = _decode_text(raw, filename)
    try:
        rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"{filename}: CSV 解析失败（{exc}）") from exc
    body = [r for r in rows if any(c for c in r)]
    if not body:
        raise ParseError(f"{filename}: CSV 内容为空")
    return _rows_to_markdown(body)


def _parse_pptx(raw: bytes, filename: str) -> str:
    from pptx import Presentation

    try:
        deck = Presentation(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"{filename}: PPT 解析失败（{exc}）") from exc

    parts: list[str] = []
    for i, slide in enumerate(deck.slides, start=1):
        block: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    block.append(text)
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text.strip() for c in row.cells] for row in shape.table.rows]
                if rows:
                    block.append(_rows_to_markdown(rows))
        if block:
            parts.append(f"【第 {i} 页】\n" + "\n".join(block))
    if not parts:
        raise ParseError(f"{filename}: 演示文稿里没有可抽取的文字")
    return "\n\n".join(parts)


def parse_html(raw: bytes, filename: str = "页面") -> str:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(raw, "html.parser")
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"{filename}: HTML 解析失败（{exc}）") from exc

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    body = soup.body or soup
    text = body.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n")]
    body_text = "\n".join(ln for ln in lines if ln)
    if not body_text:
        raise ParseError(f"{filename}: 页面没有可用正文")
    return f"# {title}\n\n{body_text}" if title else body_text


# ---------------------------------------------------------------- 入口


def parse_document(raw: bytes, filename: str) -> str:
    """按扩展名分发解析，返回清洗后的纯文本。失败抛 ParseError。"""
    if len(raw) > MAX_BYTES:
        raise ParseError(f"{filename}: 文件超过 {MAX_BYTES // 1024 // 1024}MB 上限")

    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return clean_text(_parse_pdf(raw, filename))
    if ext == ".docx":
        return clean_text(_parse_docx(raw, filename))
    if ext == ".xlsx":
        return clean_text(_parse_xlsx(raw, filename))
    if ext == ".pptx":
        return clean_text(_parse_pptx(raw, filename))
    if ext == ".csv":
        return clean_text(_parse_csv(raw, filename))
    if ext in (".html", ".htm"):
        return clean_text(parse_html(raw, filename))
    if ext in (".txt", ".md", ".markdown"):
        return clean_text(_decode_text(raw, filename))
    if ext == ".doc":
        raise ParseError(f"{filename}: 旧版 .doc 不支持，请在 Word 中另存为 .docx 后上传")
    raise ParseError(f"{filename}: 不支持的格式（支持 {_EXT_LABEL}）")


def fetch_url(url: str, *, timeout: float = 15.0) -> tuple[str, str]:
    """抓网页并抽正文，返回 (标题文件名, 正文)。仅支持 http/https。"""
    import urllib.parse
    import urllib.request

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ParseError(f"{url}: 只支持 http/https 链接")

    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; EinsteinBot/0.2)"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_URL_BYTES + 1)
            charset = resp.headers.get_content_charset() or "utf-8"
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"{url}: 抓取失败（{exc}）") from exc
    if len(raw) > MAX_URL_BYTES:
        raise ParseError(f"{url}: 页面超过 {MAX_URL_BYTES // 1024 // 1024}MB 上限")

    try:
        html = raw.decode(charset, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")

    name = parsed.netloc + parsed.path.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
    return (name or parsed.netloc), clean_text(parse_html(html.encode("utf-8"), url))
