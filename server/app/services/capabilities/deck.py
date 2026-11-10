"""演示文稿的可编辑模型：pptx ↔ 结构化 deck。

产品里的 PPT 不是"生成完就交付"的二进制，而是能被界面继续改的东西。
这里做两件事：

1. **读**：优先用生成时留下的 sidecar（完整语义，无损）；没有 sidecar
   就把任意 pptx 尽力还原成同一套 deck 结构——版式靠字号、位置、表格
   形状做启发式判断。
2. **写**：用 ppt.py 的版式系统按 deck 重新渲染，覆盖原文件并刷新
   sidecar。所以每次保存都是"重新排版"而不是在二进制里挪文本框，
   改完不会出现版式错位；代价是外部 pptx 第一次保存会被归一成本产品
   的主题样式（接口里用 editable=partial 标明，前端要提示用户）。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from pptx import Presentation

from app.services.capabilities import ppt, sandbox, slidep

DECK_DIR = ".deck"
MAX_SLIDES = 80
MAX_BULLETS = 40
MAX_CELLS_COLS = 8
MAX_CELLS_ROWS = 30
# 单页 DSL 上限：防误传超大 payload，正常一页几 KB 足够
DSL_MAX_CHARS = 120_000

LAYOUTS = (
    "title", "section", "bullets", "two_col", "quote", "table",
    "stat", "chart", "timeline", "grid", "vs", "end",
)


# ---------- sidecar ----------

def _key(rel: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", (rel or "").replace("/", "__"))[:120]


def sidecar_path(root: Path, rel: str) -> Path:
    return root / DECK_DIR / (_key(rel) + ".json")


def remember(root: Path, rel: str, payload: dict) -> None:
    """记住生成这份 deck 的结构化来源，之后编辑可无损还原。"""
    path = sidecar_path(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {**payload, "_path": rel, "_saved": time.time()}
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _read_sidecar(root: Path, rel: str, deck_mtime: float) -> dict | None:
    path = sidecar_path(root, rel)
    if not path.is_file():
        return None
    # pptx 比 sidecar 新，说明文件被外部改过，sidecar 已过期
    if path.stat().st_mtime + 0.5 < deck_mtime:
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("slides"):
        return None
    return data


# ---------- slidep DSL 清单（第二类 sidecar） ----------

def _read_dsl_manifest(root: Path, rel: str, deck_mtime: float) -> dict | None:
    """读 slidep 逐页 DSL 清单；pptx 比 manifest 新视为外部改过，作废。"""
    path = sidecar_path(root, rel)
    if not path.is_file():
        return None
    if path.stat().st_mtime + 0.5 < deck_mtime:
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("_engine") not in ("slidep", "svgdeck"):
        return None
    return data


def set_dsl_manifest(
    root: Path, rel: str, engine: str, page_files: list[str], source_hash: str = "",
) -> None:
    """整体重写 DSL 清单（svgdeck 全量重建后登记每页来源）。

    结构化 sidecar（ppt_create 产物）优先，被占用时不覆盖。
    `source_hash` 是引擎侧的内容指纹，用来判断「这份 deck 是否需要重编译」；
    引擎不提供时留空（等于缓存永不命中，走完整编译，安全兜底）。
    """
    target = sandbox.resolve_in(root, rel)
    path = sidecar_path(root, rel)
    if path.is_file():
        try:
            existing = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict) and existing.get("slides"):
            return
    manifest = {
        "_engine": engine,
        "_path": rel,
        "title": target.stem,
        "pages": [
            {"page_index": i, "dsl_file": f, "dsl": ""}
            for i, f in enumerate(page_files)
        ],
    }
    if source_hash:
        manifest["_source_hash"] = source_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def remember_dsl_page(root: Path, rel: str, page_index: int, dsl_file: str, dsl: str) -> bool:
    """登记/更新 slidep 某页的 DSL 来源，之后编辑器可按页无损改写。

    同一个 sidecar 文件名被结构化清单（ppt_create 产物）占用时不覆盖——
    混合生成的文件以结构化语义为准，返回 False。page_index<0 表示追加，
    页号按 manifest 追加序号递增（要求每次 upsert 后紧跟着登记一次）。
    """
    target = sandbox.resolve_in(root, rel)
    path = sidecar_path(root, rel)
    existing: dict | None = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            existing = None
    if isinstance(existing, dict) and existing.get("slides"):
        return False  # 结构化 sidecar 优先
    manifest = (
        existing
        if isinstance(existing, dict) and existing.get("_engine") == "slidep"
        else {"_engine": "slidep", "_path": rel, "title": target.stem, "pages": []}
    )
    pages: list = manifest.setdefault("pages", [])
    if int(page_index) < 0:
        # 追加：slidep 从空 pptx 逐页 upsert 的流程里，manifest 追加序号就是真实页序。
        # 不按 pptx 页数反推——remember 与 upsert 若有先后错位（批量补登记）会算重。
        idx = len(pages)
    else:
        idx = int(page_index)
    entry = {
        "page_index": idx,
        "dsl_file": (dsl_file or "").strip(),
        "dsl": "" if (dsl_file or "").strip() else (dsl or "")[:DSL_MAX_CHARS],
    }
    if 0 <= idx < len(pages):
        pages[idx] = entry
    else:
        pages.extend([None] * (idx - len(pages)))
        pages.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return True


def _manifest_pages(root: Path, manifest: dict) -> list[dict]:
    """把清单展开成带 DSL 内容的页列表（dsl_file 优先读文件，拿不到用内联）。"""
    out: list[dict] = []
    for i, entry in enumerate(manifest.get("pages") or []):
        if not isinstance(entry, dict):
            continue  # 占位 None：该页无 DSL 记录，编辑器标为只读页
        dsl = entry.get("dsl") or ""
        rel_file = entry.get("dsl_file") or ""
        if rel_file:
            try:
                fp = sandbox.resolve_in(root, rel_file)
                dsl = fp.read_text("utf-8")[:DSL_MAX_CHARS] if fp.is_file() else ""
            except sandbox.SandboxError:
                dsl = ""
            except OSError:
                dsl = ""
        out.append({"index": i, "dsl_file": rel_file, "dsl": dsl})
    return out


# ---------- 解析 ----------

def _font_size(para) -> float | None:
    size = getattr(para.font, "size", None)
    if size is not None:
        return float(size.pt)
    for run in para.runs:
        if run.font.size is not None:
            return float(run.font.size.pt)
    return None


def _paragraphs(text_frame) -> list[tuple[str, float, int]]:
    out: list[tuple[str, float, int]] = []
    for para in text_frame.paragraphs:
        text = "".join(r.text for r in para.runs).strip()
        if not text:
            continue
        out.append((text, _font_size(para) or 18.0, int(para.level or 0)))
    return out


def _block_text(paras: list[tuple[str, float, int]]) -> str:
    return " ".join(p[0] for p in paras).strip()


def _solid_hex(fill) -> str | None:
    try:
        if fill.type is not None and int(fill.type) == 1:  # MSO_FILL.SOLID
            return str(fill.fore_color.rgb)
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _slide_bg(slide, prs) -> str | None:
    # 全屏矩形盖住背景是最常见的写法（我们的渲染器就是这么做的）
    for shape in slide.shapes:
        try:
            if shape.width >= prs.slide_width * 0.92 and shape.height >= prs.slide_height * 0.92:
                hexv = _solid_hex(shape.fill)
                if hexv:
                    return hexv
        except (AttributeError, TypeError):
            continue
    try:
        return _solid_hex(slide.background.fill)
    except (AttributeError, TypeError, ValueError):
        return None


def _nearest_theme(bg: str | None) -> tuple[str, bool]:
    if not bg or len(bg) != 6:
        return "midnight", True
    try:
        want = tuple(int(bg[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "midnight", True
    best, best_d = "midnight", None
    for key, theme in ppt.THEMES.items():
        ref = theme["bg"]
        have = tuple(int(ref[i : i + 2], 16) for i in (0, 2, 4))
        dist = sum((a - b) ** 2 for a, b in zip(want, have))
        if best_d is None or dist < best_d:
            best, best_d = key, dist
    # 色差过大时仍用最近主题，但标记为推断
    return best, best_d is not None and best_d > 6000


def _clean_bullet(text: str, level: int) -> str:
    t = re.sub(r"^[•·▪◦\-\u2013\u2014]\s*", "", text).strip()
    if not t:
        return ""
    return ("- " + t) if level > 0 else t


def _table_payload(shape) -> dict | None:
    try:
        table = shape.table
    except (AttributeError, TypeError):
        return None
    rows: list[list[str]] = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append((cell.text or "").strip().replace("\n", " "))
        rows.append(cells)
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) < 2:
        return None
    return {"headers": rows[0][:MAX_CELLS_COLS], "rows": [r[:MAX_CELLS_COLS] for r in rows[1:MAX_CELLS_ROWS]]}


def _table_title(blocks: list[dict]) -> str:
    if not blocks:
        return ""
    first = min(blocks, key=lambda b: b["top"])
    return first["paras"][0][0]


def _parse_slide(slide, prs, index: int, total: int) -> dict:
    blocks: list[dict] = []
    table: dict | None = None
    for shape in slide.shapes:
        if getattr(shape, "has_table", False):
            got = _table_payload(shape)
            if got:
                table = got
                continue
        if not getattr(shape, "has_text_frame", False):
            continue
        paras = _paragraphs(shape.text_frame)
        if not paras:
            continue
        blocks.append(
            {
                "top": int(shape.top or 0),
                "left": int(shape.left or 0),
                "width": int(shape.width or 0),
                "height": int(shape.height or 0),
                "paras": paras,
                "size": max(p[1] for p in paras),
                "text": _block_text(paras),
            }
        )

    if not blocks and not table:
        return {"layout": "section", "title": "", "subtitle": ""}

    blocks.sort(key=lambda b: (b["top"], b["left"]))
    # 标题块：字号最大，同分取最靠上
    title_block = max(blocks, key=lambda b: (b["size"], -b["top"])) if blocks else None

    if table:
        head = title_block["paras"][0][0] if (title_block and title_block["size"] >= 22) else _table_title(blocks)
        return {"layout": "table", "title": head, **table}

    if index == 0:
        title_text = title_block["paras"][0][0] if title_block else ""
        rest = [b for b in blocks if b is not title_block]
        subtitle = " ".join(p[0] for b in rest for p in b["paras"])[:200]
        return {"layout": "title", "title": title_text, "subtitle": subtitle}

    title_text = title_block["paras"][0][0]
    others = [b for b in blocks if b is not title_block]
    body_paras = [(p, b) for b in others for p in b["paras"]]

    # 结尾页
    joined = " ".join(b["text"] for b in blocks)
    if index == total - 1 and re.search(r"(谢谢|thank\s*you|q\s*&\s*a|the\s*end)", joined, re.I) and len(body_paras) <= 2:
        sub = " ".join(p[0][0] for p in body_paras)[:120]
        return {"layout": "end", "title": title_text, "subtitle": sub}

    # 单块：引用页 / 章节页
    if len(blocks) == 1:
        text = blocks[0]["text"]
        if re.match(r'^[\u201c"\u300c]', text) and len(text) < 220:
            return {"layout": "quote", "quote": text.strip("\u201c\u201d\"\u300c\u300d"), "subtitle": ""}
        if len(text) < 80 and blocks[0]["size"] >= 30:
            return {"layout": "section", "title": title_text, "subtitle": ""}

    # 两栏：左右各一簇，两块以上正文块
    if len(others) >= 2:
        mid = prs.slide_width / 2
        left = [b for b in others if b["left"] + b["width"] / 2 < mid]
        right = [b for b in others if b["left"] + b["width"] / 2 >= mid]
        if left and right and sum(len(b["paras"]) for b in left) >= 1 and sum(len(b["paras"]) for b in right) >= 1:
            cols = []
            for group in (left, right):
                head = ""
                items: list[str] = []
                for bi, b in enumerate(group):
                    for pi, (text, size, level) in enumerate(b["paras"]):
                        cleaned = _clean_bullet(text, level)
                        if not cleaned:
                            continue
                        if bi == 0 and pi == 0 and level == 0 and len(cleaned) <= 24:
                            head = cleaned
                        else:
                            items.append(cleaned)
                if not items and head:
                    head, items = "", [head]
                cols.append({"title": head, "bullets": items[:MAX_BULLETS]})
            return {"layout": "two_col", "title": title_text, "columns": cols}

    bullets: list[str] = []
    for (text, _size, level), _b in body_paras:
        cleaned = _clean_bullet(text, level)
        if cleaned:
            bullets.append(cleaned)
    # 标题块里多余的行也算正文
    if len(title_block["paras"]) > 1:
        for text, _size, level in title_block["paras"][1:]:
            cleaned = _clean_bullet(text, level)
            if cleaned:
                bullets.insert(0, cleaned)
    if not bullets and not title_text:
        return {"layout": "section", "title": "", "subtitle": ""}
    return {"layout": "bullets", "title": title_text, "bullets": bullets[:MAX_BULLETS]}


def parse_pptx(path: Path) -> tuple[dict, bool]:
    prs = Presentation(str(path))
    slides = [_parse_slide(slide, prs, i, len(prs.slides)) for i, slide in enumerate(prs.slides)]
    theme, inferred = _nearest_theme(_slide_bg(prs.slides[0], prs) if len(prs.slides) else None)
    title = ""
    subtitle = ""
    if slides and slides[0].get("layout") == "title":
        title = slides[0].get("title", "")
        subtitle = slides[0].get("subtitle", "")
    payload = {"title": title or path.stem, "subtitle": subtitle, "author": "", "theme": theme, "slides": slides}
    return payload, inferred


# ---------- 规范化 ----------

def _text(value, limit: int = 400) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)[:limit]
    return str(value).strip()[:limit]


def _lines(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: list = [ln for ln in re.split(r"\n", value)]
    elif isinstance(value, dict):
        items = [f"{k}：{v}" for k, v in value.items()]
    else:
        items = [str(x) for x in value]
    out = [str(ln).strip()[:400] for ln in items if str(ln).strip()]
    return out[:MAX_BULLETS]


def normalize(payload: dict) -> dict:
    """只接受白名单字段并裁长度——前端传来的东西一律不可信。"""
    theme = _text(payload.get("theme"), 24).lower()
    if theme not in ppt.THEMES:
        theme = "midnight"
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raw_slides = [{"layout": "title", "title": _text(payload.get("title")) or "演示文稿"}]
    slides: list[dict] = []
    for item in raw_slides[:MAX_SLIDES]:
        if not isinstance(item, dict):
            continue
        layout = _text(item.get("layout"), 20).lower()
        if layout not in LAYOUTS:
            # 未写/写错版式时按字段推断（封面、数据页、结尾页不再一律降级成 bullets）
            layout = ppt._infer_layout(item if isinstance(item, dict) else {}, len(slides), len(raw_slides[:MAX_SLIDES]))
            if layout not in LAYOUTS:
                layout = "bullets"
        slide: dict = {"layout": layout}
        for key in ("title", "subtitle", "kicker", "number", "quote", "variant", "note"):
            value = _text(item.get(key))
            if value:
                slide[key] = value
        bullets = _lines(item.get("bullets"))
        if bullets:
            slide["bullets"] = bullets
        # 可视化版式的结构化字段
        stats_raw = item.get("stats")
        if isinstance(stats_raw, list):
            stats = []
            for raw in stats_raw[:4]:
                if isinstance(raw, dict):
                    stats.append({"value": _text(raw.get("value"), 14), "label": _text(raw.get("label"), 18),
                                  "note": _text(raw.get("note"), 40)})
                elif isinstance(raw, str) and raw.strip():
                    parts = raw.strip().split("：", 1)
                    stats.append({"value": _text(parts[0], 14),
                                  "label": _text(parts[1], 18) if len(parts) > 1 else "", "note": ""})
            stats = [x for x in stats if x["value"]]
            if stats:
                slide["stats"] = stats
        items_raw = item.get("items")
        if isinstance(items_raw, list):
            items = []
            for raw in items_raw[:7]:
                if isinstance(raw, dict):
                    items.append({"label": _text(raw.get("label"), 22), "value": _text(raw.get("value"), 16)})
                elif isinstance(raw, str) and "：" in raw:
                    label, value = raw.split("：", 1)
                    items.append({"label": _text(label, 22), "value": _text(value, 16)})
            items = [x for x in items if x["label"]]
            if items:
                slide["items"] = items
        steps_raw = item.get("steps")
        if isinstance(steps_raw, list):
            steps = []
            for raw in steps_raw[:5]:
                if isinstance(raw, dict):
                    steps.append({"label": _text(raw.get("label"), 14), "text": _text(raw.get("text"), 60)})
                elif isinstance(raw, str) and raw.strip():
                    parts = raw.strip().split("：", 1)
                    steps.append({"label": _text(parts[0], 14),
                                  "text": _text(parts[1], 60) if len(parts) > 1 else ""})
            if steps:
                slide["steps"] = steps
        cards_raw = item.get("cards")
        if isinstance(cards_raw, list):
            cards = []
            for raw in cards_raw[:6]:
                if isinstance(raw, dict):
                    cards.append({"title": _text(raw.get("title"), 20), "text": _text(raw.get("text"), 90)})
                elif isinstance(raw, str) and raw.strip():
                    parts = raw.strip().split("：", 1)
                    cards.append({"title": _text(parts[0], 20),
                                  "text": _text(parts[1], 90) if len(parts) > 1 else ""})
            if cards:
                slide["cards"] = cards
        for side_key in ("left", "right"):
            raw = item.get(side_key)
            if isinstance(raw, dict):
                slide[side_key] = {"title": _text(raw.get("title"), 24), "bullets": _lines(raw.get("bullets"))}
            elif isinstance(raw, str) and raw.strip():
                slide[side_key] = {"title": "", "bullets": _lines(raw)}
        columns = item.get("columns")
        if isinstance(columns, list) and columns:
            cols = []
            for col in columns[:2]:
                if isinstance(col, dict):
                    cols.append({"title": _text(col.get("title"), 120), "bullets": _lines(col.get("bullets"))})
                elif isinstance(col, str):
                    cols.append({"title": "", "bullets": _lines(col)})
                else:
                    cols.append({"title": "", "bullets": []})
            slide["columns"] = cols
        headers = _lines(item.get("headers"))[:MAX_CELLS_COLS]
        rows: list[list[str]] = []
        raw_rows = item.get("rows")
        if headers and isinstance(raw_rows, list):
            for row in raw_rows[:MAX_CELLS_ROWS]:
                if isinstance(row, (list, tuple)):
                    rows.append([_text(cell, 200) for cell in list(row)[:MAX_CELLS_COLS]])
                elif isinstance(row, str):
                    rows.append([_text(x, 200) for x in row.split("|")][:MAX_CELLS_COLS])
        if headers:
            slide["headers"] = headers
            slide["rows"] = rows
        slides.append(slide)
    if not slides:
        slides = [{"layout": "title", "title": "演示文稿"}]
    return {
        "title": _text(payload.get("title"), 200) or _text(slides[0].get("title")) or "演示文稿",
        "subtitle": _text(payload.get("subtitle"), 300),
        "author": _text(payload.get("author"), 120),
        "theme": theme,
        "slides": slides,
    }


# ---------- 对外 ----------

def _describe(root: Path, rel: str, payload: dict, source: str, inferred: bool, target: Path) -> dict:
    stat = target.stat()
    return {
        "path": rel,
        "title": payload.get("title") or "",
        "subtitle": payload.get("subtitle") or "",
        "author": payload.get("author") or "",
        "theme": payload.get("theme") or "midnight",
        "theme_inferred": inferred,
        "source": source,
        # sidecar 来源 = 完整语义，保存无损；解析来源 = 归一化重排
        "editable": "full" if source == "sidecar" else "partial",
        "slides": payload.get("slides") or [],
        "preview_images": payload.get("preview_images") or [],
        "bytes": stat.st_size,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
    }


def _slide_images(root: Path, target: Path) -> list[str]:
    """用本机 PowerPoint 把每页导出成 JPG，返回相对 root 的路径列表。

    编辑器的结构化模型是有损的（丢渐变/圆角/玻璃卡/透明度），前端「设计稿」
    模式靠这份真实渲染图还原文件本身的设计。导出按文件 mtime 缓存；机器上
    没有 PowerPoint 或 COM 失败时返回空列表，前端自动退回大纲模式。
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return []
    preview_root = root / ".deckpreview"
    mtime_int = int(target.stat().st_mtime)
    cache_dir = preview_root / f"{target.stem}-{mtime_int}"
    # 导出失败标记（WPS 兼容注册的机器上 COM SaveAs 可能永远被拒呼）：
    # 同一 mtime 失败过就不再重试，避免每次 load 都白耗几秒
    marker = preview_root / f"{target.stem}.failed-{mtime_int}"
    # 每个文件只保留最新一份渲染缓存：命中旧 mtime 缓存说明文件已重生成，先清掉历史目录
    if preview_root.is_dir():
        for stale in preview_root.glob(f"{target.stem}-*"):
            if stale != cache_dir and stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
        for old_marker in preview_root.glob(f"{target.stem}.failed-*"):
            if old_marker != marker:
                try:
                    old_marker.unlink()
                except OSError:
                    pass  # 清理失败不影响主流程（Windows 上文件常被占用）
    if marker.exists():
        return []
    if not cache_dir.is_dir() or not any(cache_dir.glob("*.JPG")):
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import pywintypes
        except ImportError:
            return []
        try:
            pythoncom.CoInitialize()
            try:
                # EnsureDispatch 生成类型缓存：PowerPoint 2007 上纯动态派发的
                # Presentations.Open 会解析失败（AttributeError: Open.SaveAs）
                try:
                    app = win32com.client.gencache.EnsureDispatch("PowerPoint.Application")
                except Exception:  # noqa: BLE001 — gen_py 缓存损坏时退回动态派发
                    app = win32com.client.Dispatch("PowerPoint.Application")
                pres = None
                for attempt in range(3):
                    try:
                        pres = app.Presentations.Open(str(target), True, False, False)
                        pres.SaveAs(str(cache_dir), 17)  # ppSaveAsJPG：整份导出为逐页 JPG
                        break
                    except pywintypes.com_error:
                        # PowerPoint 启动中会拒呼（RPC_E_CALL_REJECTED），退避重试
                        if attempt == 2:
                            raise
                        time.sleep(1.0)
                if pres is not None:
                    pres.Close()
            finally:
                try:
                    # 只在没别的演示文稿打开时退出，避免误杀用户自己的 PowerPoint 窗口
                    if app.Presentations.Count == 0:
                        app.Quit()
                except Exception:  # noqa: BLE001 — 退出失败不影响导出结果
                    pass
                pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001 — 预览是增强能力，失败不阻塞大纲
            try:
                preview_root.mkdir(parents=True, exist_ok=True)
                marker.write_text("export failed", encoding="utf-8")
            except OSError:
                pass  # 清理失败不影响主流程（Windows 上文件常被占用）
            return []
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass  # 清理失败不影响主流程（Windows 上文件常被占用）
    jpgs = sorted(
        cache_dir.glob("*.JPG"),
        key=lambda p: (len(p.stem), p.stem),  # 幻灯片1 < 幻灯片10（位数优先）
    )
    if not jpgs:
        return []
    return [p.relative_to(root).as_posix() for p in jpgs]


def _describe_dsl(root: Path, rel: str, manifest: dict, pages: list[dict], target: Path) -> dict:
    stat = target.stat()
    return {
        "path": rel,
        "title": manifest.get("title") or target.stem,
        "subtitle": "",
        "author": "",
        "theme": "",
        "theme_inferred": False,
        "source": "sidecar",
        # DSL 即真相源：改 DSL → lint → 重编译，全链路无损（slidep / svgdeck 同机制）
        "editable": "full",
        "engine": manifest.get("_engine", "slidep"),
        "slides": [
            {"layout": "section", "title": f"第 {p['index'] + 1} 页"} for p in pages
        ],
        "pages": pages,
        "preview_images": manifest.get("preview_images") or [],
        "bytes": stat.st_size,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
    }


def load_deck(root: Path, rel: str) -> dict:
    target = sandbox.resolve_in(root, rel)
    if not target.is_file():
        raise sandbox.SandboxError(f"文件不存在：{rel}")
    if target.suffix.lower() != ".pptx":
        raise sandbox.SandboxError("只支持 .pptx 演示文稿")
    mtime = target.stat().st_mtime
    manifest = _read_dsl_manifest(root, rel, mtime)
    if manifest is not None:
        pages = _manifest_pages(root, manifest)
        manifest["preview_images"] = _slide_images(root, target)
        return _describe_dsl(root, rel, manifest, pages, target)
    payload = _read_sidecar(root, rel, mtime)
    if payload is not None:
        # sidecar 可能缺 layout（生成时靠推断渲染），读取时补齐，编辑器才能正确预览
        slides = payload.get("slides") or []
        total = len(slides)
        for idx, slide in enumerate(slides):
            if isinstance(slide, dict) and slide.get("layout") not in LAYOUTS:
                slide["layout"] = ppt._infer_layout(slide, idx, total)
        payload["preview_images"] = _slide_images(root, target)
        return _describe(root, rel, payload, "sidecar", False, target)
    parsed, inferred = parse_pptx(target)
    parsed["preview_images"] = _slide_images(root, target)
    return _describe(root, rel, parsed, "parsed", inferred, target)


def save_deck(root: Path, rel: str, payload: dict) -> dict:
    rel = (rel or "").strip().replace("\\", "/")
    if not rel.lower().endswith(".pptx"):
        raise sandbox.SandboxError("保存路径必须以 .pptx 结尾")
    clean = normalize(payload)
    out = sandbox.resolve_in(root, rel)
    if out.exists() and not out.is_file():
        raise sandbox.SandboxError("目标路径是目录")
    ppt.write_pptx(clean, out)
    remember(root, rel, clean)
    return load_deck(root, rel)


def apply_dsl_page(root: Path, rel: str, page_index: int, dsl: str, apply: bool = True) -> dict:
    """浏览页 DSL 模式的保存链路：lint → 有 error 拒绝编译 → 重编译替换该页。

    apply=False 只校验不落盘。返回 {ok, diagnostics, deck?}，diagnostics
    透传 slidep 的 ruleId/severity/message，前端按 severity 分色展示。
    """
    target = sandbox.resolve_in(root, rel)
    if not target.is_file() or target.suffix.lower() != ".pptx":
        raise sandbox.SandboxError(f"文件不存在或不是 pptx：{rel}")
    dsl = (dsl or "").strip()
    if not dsl:
        raise sandbox.SandboxError("DSL 内容为空")
    if len(dsl) > DSL_MAX_CHARS:
        raise sandbox.SandboxError(f"单页 DSL 超过 {DSL_MAX_CHARS} 字符上限")
    manifest = _read_dsl_manifest(root, rel, target.stat().st_mtime)
    engine = (manifest or {}).get("_engine", "slidep")
    if engine == "svgdeck":
        return _apply_svg_page(root, rel, int(page_index), dsl, apply, manifest)
    lint = slidep.lint(target, dsl, max(int(page_index), 0))
    diagnostics = lint.get("diagnostics") or []
    has_error = (lint.get("ok") is False and not diagnostics) or any(
        isinstance(d, dict) and str(d.get("severity", "")).lower() in {"error", "fatal"}
        for d in diagnostics
    )
    if not apply or has_error:
        return {"ok": not has_error, "diagnostics": diagnostics}
    up = slidep.upsert(target, dsl, int(page_index))
    if not up.get("ok"):
        return {
            "ok": False,
            "diagnostics": [{"ruleId": "compile", "severity": "error",
                             "message": up.get("error") or "编译失败"}],
        }
    remember_dsl_page(root, rel, int(page_index), "", dsl)
    return {"ok": True, "diagnostics": diagnostics, "deck": load_deck(root, rel)}


def _apply_svg_page(
    root: Path, rel: str, page_index: int, svg: str, apply: bool, manifest: dict | None
) -> dict:
    """svgdeck 产物的编辑链路：轻校验 → 落盘该页 SVG → 整体重编译。"""
    from app.services.capabilities import svgdeck  # 局部导入避免环

    lint = svgdeck.lint_svg(svg)
    diagnostics = lint.get("diagnostics") or []
    if not apply or not lint.get("ok"):
        return {"ok": lint.get("ok", False), "diagnostics": diagnostics}
    pages = [p for p in (manifest or {}).get("pages") or [] if isinstance(p, dict)]
    files = [p.get("dsl_file") or "" for p in pages]
    idx = page_index if 0 <= page_index < len(files) else len(files)
    # dsl_file 形如 slides/NN.svg；编辑器提交的内容直接覆盖该文件
    if idx < len(files) and files[idx]:
        page_path = sandbox.resolve_in(root, files[idx])
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(svg, encoding="utf-8")
    else:
        svgdeck.write_page(root, idx + 1, svg)
    try:
        svgdeck.compile_deck(root, rel)
    except sandbox.SandboxError as exc:
        return {
            "ok": False,
            "diagnostics": [{"ruleId": "compile", "severity": "error",
                             "message": str(exc)}],
        }
    return {"ok": True, "diagnostics": diagnostics, "deck": load_deck(root, rel)}


def list_themes() -> list[dict]:
    return [
        {
            "id": key,
            "name": theme["name"],
            "bg": theme["bg"],
            "surface": theme["surface"],
            "accent": theme["accent"],
            "accent2": theme["accent2"],
            "on_dark": theme["on_dark"],
            "on_dark_muted": theme["on_dark_muted"],
            "ink": theme["ink"],
            "ink_muted": theme["ink_muted"],
            "paper": theme["paper"],
        }
        for key, theme in ppt.THEMES.items()
    ]
