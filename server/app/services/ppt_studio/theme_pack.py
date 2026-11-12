"""主题包：解析用户上传的 .pptx 模板，抽出可直接驱动生成的主题资产。

python-pptx 的高层 API 拿不到主题（clrScheme / fontScheme），唯一可靠路径是
直接读包内的 `ppt/theme/theme1.xml`（经 `iter_parts()` 定位、lxml 解析），
`sysClr` 取 `lastClr` 兜底。保真度报告按 P1-2 口径：
`score = 100 - Σpenalty`，clamp 到 40~100。

源文件长期保留在 `data/ppt_templates/<workspace>/<pack_id>.pptx`：
「反用模板自身」生成时它就是基底（Presentation(BytesIO)），删记录时才删文件。
"""

from __future__ import annotations

import io
import json
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree
from pptx import Presentation

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import PPT_THEME_PACKS
from app.services.capabilities.sandbox import slugify
from app.services.ppt_studio import designs, template_style
from app.services.state import JsonState

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON = JsonState(_DATA_DIR / "ppt_theme_packs.json")
_lock = threading.Lock()

# 单文件 32MB：与沙箱 MAX_BINARY_BYTES 同口径，超限文案带具体数字
MAX_TEMPLATE_BYTES = 32 * 1024 * 1024

# 版式 kind 判定（名字小写后关键词匹配，供确认卡的 layout_hint 匹配）
_KIND_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("封面", "cover"),
    ("title slide", "cover"),
    ("标题幻灯片", "cover"),
    ("节", "section"),
    ("section", "section"),
    ("章节", "section"),
    ("标题和内容", "bullets"),
    ("title and content", "bullets"),
    ("内容", "bullets"),
    ("content", "bullets"),
    ("两栏", "two_col"),
    ("two content", "two_col"),
    ("比较", "two_col"),
    ("comparison", "two_col"),
    ("引用", "quote"),
    ("quote", "quote"),
    ("空白", "blank"),
    ("blank", "blank"),
)

# title/body 类占位符（决定 has_title / has_body）
_TITLE_PH = {"TITLE", "CENTER_TITLE"}
_BODY_PH = {"BODY", "OBJECT", "SUBTITLE"}

# 保真度扣分表（见 ARCHITECTURE.md 3.2）
_PENALTY = {
    "smartart": (6, 18),
    "chart": (5, 15),
    "picture_effect": (2, 6),
    "irregular_master": (12, 12),
    "theme_missing": (20, 20),
    "footer_missing": (3, 3),
}

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SMARTART_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"

DEFAULT_PALETTE: dict[str, str] = {
    "dk1": "000000",
    "lt1": "FFFFFF",
    "dk2": "1F2937",
    "lt2": "F3F4F6",
    "accent1": "1F4E79",
    "accent2": "2E75B6",
    "accent3": "A5A5A5",
    "accent4": "FFC000",
    "accent5": "5B9BD5",
    "accent6": "70AD47",
    "hlink": "0563C1",
    "folHlink": "954F72",
}

DEFAULT_FONTS: dict[str, str] = {
    "major_latin": "Calibri Light",
    "major_ea": "Microsoft YaHei",
    "minor_latin": "Calibri",
    "minor_ea": "Microsoft YaHei",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _local(tag: str) -> str:
    """`{ns}clrScheme` → `clrScheme`（不带命名空间前缀的本地名）。"""
    return tag.rsplit("}", 1)[-1]


def _kind_of(name: str) -> str:
    low = (name or "").strip().lower()
    for keyword, kind in _KIND_KEYWORDS:
        if keyword in low:
            return kind
    return "other"


def _ph_kind(ph_type: str) -> str:
    if ph_type in _TITLE_PH:
        return "title"
    if ph_type in _BODY_PH:
        return "body"
    return "other"


def _parse_theme_xml(blob: bytes) -> tuple[dict[str, str], dict[str, str]]:
    """theme1.xml → (palette, fonts)。缺失项用默认值补齐。"""
    palette = dict(DEFAULT_PALETTE)
    fonts = dict(DEFAULT_FONTS)
    try:
        root = etree.fromstring(blob)
    except etree.XMLSyntaxError:
        return palette, fonts

    scheme = root.find(f".//{{{_A_NS}}}clrScheme")
    if scheme is not None:
        for child in scheme:
            name = _local(child.tag)
            if name not in palette:
                continue
            srgb = child.find(f"{{{_A_NS}}}srgbClr")
            if srgb is not None and srgb.get("val"):
                palette[name] = srgb.get("val", "").upper()
                continue
            sys_el = child.find(f"{{{_A_NS}}}sysClr")
            # sysClr（系统色）没有固定 RGB，取 lastClr 兜底
            if sys_el is not None and sys_el.get("lastClr"):
                palette[name] = sys_el.get("lastClr", "").upper()

    font_scheme = root.find(f".//{{{_A_NS}}}fontScheme")
    if font_scheme is not None:
        for group, prefix in (("majorFont", "major"), ("minorFont", "minor")):
            node = font_scheme.find(f"{{{_A_NS}}}{group}")
            if node is None:
                continue
            latin = node.find(f"{{{_A_NS}}}latin")
            if latin is not None and (latin.get("typeface") or "").strip():
                fonts[f"{prefix}_latin"] = latin.get("typeface", "").strip()
            # 中文（ea）缺失/为空时回落微软雅黑，否则中文渲染会掉回宋体
            ea = node.find(f"{{{_A_NS}}}ea")
            if ea is not None and (ea.get("typeface") or "").strip():
                fonts[f"{prefix}_ea"] = ea.get("typeface", "").strip()
    return palette, fonts


def _scan_fidelity(prs: Presentation) -> tuple[dict, list[dict], list[str]]:
    """扫描 SmartArt / 图表 / 图片特效 / 母版形态，产出 FidelityReport。"""
    issues: list[dict] = []
    degradations: list[str] = []
    counts = {"smartart": 0, "chart": 0, "picture_effect": 0}

    for slide in prs.slides:
        xml = slide._element.xml
        smartart_n = xml.count(_SMARTART_URI)
        if smartart_n:
            counts["smartart"] += smartart_n
            issues.append({
                "kind": "smartart",
                "slide_index": list(prs.slides).index(slide),
                "detail": f"检测到 {smartart_n} 处 SmartArt 图形",
                "fallback": "转为要点列表",
            })
        chart_n = xml.count(_CHART_URI)
        if chart_n:
            counts["chart"] += chart_n
            issues.append({
                "kind": "chart",
                "slide_index": list(prs.slides).index(slide),
                "detail": f"检测到 {chart_n} 处内嵌图表",
                "fallback": "转为条形图近似或表格",
            })
        shadow_n = xml.count("<a:outerShdw")
        if shadow_n:
            counts["picture_effect"] += shadow_n
            issues.append({
                "kind": "picture_effect",
                "slide_index": list(prs.slides).index(slide),
                "detail": f"检测到 {shadow_n} 处阴影/特效",
                "fallback": "保留占位说明",
            })

    layouts = _collect_layouts(prs)
    body_layouts = [l for l in layouts if l["has_body"]]
    if layouts and not body_layouts:
        issues.append({
            "kind": "irregular_master",
            "slide_index": -1,
            "detail": "所有版式都没有正文占位符",
            "fallback": "绝对定位文本框排版",
        })

    has_slide_number = _has_slide_number(prs)
    if not has_slide_number:
        issues.append({
            "kind": "footer_missing",
            "slide_index": -1,
            "detail": "母版没有页码占位符",
            "fallback": "不做页眉页脚，仅页码",
        })

    score = 100
    for kind, count in counts.items():
        per, cap = _PENALTY[kind]
        score -= min(per * count, cap)
    if any(i["kind"] == "irregular_master" for i in issues):
        score -= _PENALTY["irregular_master"][0]
    if any(i["kind"] == "footer_missing" for i in issues):
        score -= _PENALTY["footer_missing"][0]
    score = _clamp(score, 40, 100)

    if counts["smartart"]:
        degradations.append(f"{counts['smartart']} 处 SmartArt 将转为要点列表")
    if counts["chart"]:
        degradations.append(f"{counts['chart']} 处内嵌图表将转为条形图近似或表格")
    no_body = len([l for l in layouts if not l["has_body"]])
    if no_body:
        degradations.append(f"{no_body} 个版式无内容占位符，将用绝对定位补文本框")
    if not has_slide_number:
        degradations.append("母版无页码占位符，页脚仅保留页码")

    report = {
        "score": score,
        "issues": issues,
        "degradations": degradations,
        "summary_zh": f"{len(layouts)} 个版式完整解析" + (
            f"；{len(issues)} 处降级" if issues else "，无降级项"
        ),
    }
    return report, issues, degradations


def _collect_layouts(prs: Presentation) -> list[dict]:
    """全部母版 × 版式 → LayoutInfo 列表（占位符坐标按英寸）。"""
    layouts: list[dict] = []
    index = 0
    emu = 914400.0
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            placeholders: list[dict] = []
            has_title = False
            has_body = False
            for ph in layout.placeholders:
                ph_type = str(ph.placeholder_format.type).split(".")[-1].split(" ")[0]
                box = {
                    "idx": int(ph.placeholder_format.idx),
                    "ph_type": ph_type,
                    "left": round((ph.left or 0) / emu, 2),
                    "top": round((ph.top or 0) / emu, 2),
                    "width": round((ph.width or 0) / emu, 2),
                    "height": round((ph.height or 0) / emu, 2),
                }
                placeholders.append(box)
                kind = _ph_kind(ph_type)
                if kind == "title":
                    has_title = True
                elif kind == "body":
                    has_body = True
            layouts.append({
                "index": index,
                "name": layout.name or f"版式 {index}",
                "kind": _kind_of(layout.name or ""),
                "placeholders": placeholders,
                "has_title": has_title,
                "has_body": has_body,
            })
            index += 1
    return layouts


def _has_slide_number(prs: Presentation) -> bool:
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            for ph in layout.placeholders:
                if "SLIDE_NUMBER" in str(ph.placeholder_format.type):
                    return True
    return False


def _header_footer(prs: Presentation) -> dict:
    """页眉页脚探测：页码占位符 + 幻灯片底部短文本采样（过滤提示语）。"""
    snippets: list[str] = []
    seen: set[str] = set()
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = (shape.text_frame.text or "").strip()
            if not text or len(text) > 30 or text in seen:
                continue
            # 母版提示语（"单击此处编辑…"）不是真实页脚内容
            if "单击此处" in text or "Click to edit" in text:
                continue
            # 只采样版心底部（>85% 高度）的短文本，视为页脚片段
            top_ratio = (shape.top or 0) / max(prs.slide_height or 1, 1)
            if top_ratio > 0.85:
                seen.add(text)
                snippets.append(text)
    return {
        "has_slide_number": _has_slide_number(prs),
        "has_date": False,
        "snippets": snippets[:5],
    }


def parse_theme_pack(raw: bytes, filename: str) -> dict:
    """解析 .pptx → 主题包 dict（不含 id / workspace / 时间戳）。

    任何解析失败都抛 `ValueError`（路由层转 400 `template_invalid`）。
    """
    if not raw:
        raise ValueError("文件为空")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            if zf.testzip() is not None:
                raise ValueError("zip 结构损坏")
    except zipfile.BadZipFile as exc:
        raise ValueError("不是合法的 .pptx（zip）文件") from exc

    try:
        prs = Presentation(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 — python-pptx 对损坏包抛的异常类型很杂
        raise ValueError(f"无法解析演示文稿：{exc}") from exc

    theme_part = None
    for part in prs.part.package.iter_parts():
        name = str(part.partname)
        if name.startswith("/ppt/theme/theme"):
            theme_part = part
            break

    if theme_part is not None:
        palette, fonts = _parse_theme_xml(theme_part.blob)
        theme_missing = False
    else:
        palette, fonts = dict(DEFAULT_PALETTE), dict(DEFAULT_FONTS)
        theme_missing = True

    report, issues, degradations = _scan_fidelity(prs)
    if theme_missing:
        report["score"] = _clamp(report["score"] - _PENALTY["theme_missing"][0], 40, 100)
        report["issues"].append({
            "kind": "theme_missing",
            "slide_index": -1,
            "detail": "包内没有主题定义（theme1.xml）",
            "fallback": "退化为内置 midnight 主题",
        })
        report["degradations"].append("缺少主题定义，配色退化为内置午夜蓝")
        report["summary_zh"] = f"{report['summary_zh']}；主题缺失"

    layouts = _collect_layouts(prs)
    status = "failed" if not layouts else ("partial" if issues else "ready")

    emu = 914400.0
    slide_size = {
        "width_in": round((prs.slide_width or 0) / emu, 3),
        "height_in": round((prs.slide_height or 0) / emu, 3),
    }
    name = (Path(filename).stem or "公司模板").strip()[:60]
    try:
        style = template_style.extract(
            prs, palette=palette, fonts=fonts, layouts=layouts, slide_size=slide_size, name=name,
        )
    except Exception:  # noqa: BLE001 — 抽取失败不该让上传失败，主题色板兜底仍可生成
        logger.exception("模板风格抽取失败，回落主题色板：%s", filename)
        style = template_style.fallback(palette, fonts, slide_size, name=name)
    # 适配说明进保真度报告：用户要在上传时就看到「字体被换了」「比例被归一了」
    for note in style["report"].get("notes") or []:
        if note not in report["degradations"]:
            report["degradations"].append(note)

    return {
        "name": name,
        "source_filename": filename,
        "status": status,
        "palette": palette,
        "fonts": fonts,
        "layouts": layouts,
        "style": style,
        "header_footer": _header_footer(prs),
        "fidelity": report,
        "meta": {
            "slide_size": slide_size,
            "theme_missing": theme_missing,
        },
    }


def safe_filename(name: str) -> str:
    """deck 落盘的安全文件名（与 capabilities/ppt.py 同一清洗规则）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "-", (name or "").strip()).strip("-")
    return (cleaned or "presentation")[:60]


# ---------------------------------------------------------------- store（双后端）

def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table() -> TableState:
    db = get_db()
    migrate(db)
    return TableState(PPT_THEME_PACKS, db)


def _all() -> list[dict]:
    if _use_table():
        return _table().rows()
    return [r for r in (_JSON.read() or []) if isinstance(r, dict)]


def _save_all(rows: list[dict]) -> None:
    if _use_table():
        _table().write({r["id"]: r for r in rows})
    else:
        _JSON.write(rows)


def _template_dir(workspace_id: str) -> Path:
    return _DATA_DIR / "ppt_templates" / slugify(workspace_id or "default")


def _save_source(workspace_id: str, pack_id: str, raw: bytes) -> str:
    """源 pptx 落盘并返回存储路径（相对 data/ 的展示用路径）。"""
    directory = _template_dir(workspace_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{pack_id}.pptx"
    target.write_bytes(raw)
    return str(target.relative_to(_DATA_DIR)).replace("\\", "/")


def _source_abs_path(source_path: str) -> Path:
    return _DATA_DIR / source_path


def list_packs(workspace_id: str, owner_id: str) -> list[dict]:
    """workspace 级共享：同空间全部可见（拍板结论 #1）。新的在前。"""
    rows = [p for p in _all() if p.get("workspace_id") == workspace_id]
    rows.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return rows


def get_pack(pack_id: str) -> dict | None:
    return next((p for p in _all() if p.get("id") == pack_id), None)


def get_visible_pack(pack_id: str, workspace_id: str) -> dict | None:
    """跨工作区的包一律当不存在（不泄露存在性）。"""
    pack = get_pack(pack_id)
    if pack is None or pack.get("workspace_id") != workspace_id:
        return None
    return pack


def create_pack(
    *, workspace_id: str, owner_id: str, raw: bytes, filename: str, name: str = "",
) -> dict:
    """解析 + 落盘 + 入库。解析失败抛 ValueError（路由转 400）。"""
    parsed = parse_theme_pack(raw, filename)
    pack_id = f"tp_{uuid.uuid4().hex[:6]}"
    source_path = _save_source(workspace_id, pack_id, raw)
    entry = {
        "id": pack_id,
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "name": (name or parsed["name"]).strip()[:60],
        "source_filename": filename,
        "source_path": source_path,
        "status": parsed["status"],
        "palette": parsed["palette"],
        "fonts": parsed["fonts"],
        "layouts": parsed["layouts"],
        "style": parsed["style"],
        "meta": {**parsed["meta"], "header_footer": parsed["header_footer"]},
        "fidelity": parsed["fidelity"],
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        rows = _all()
        rows.append(entry)
        _save_all(rows)
    return entry


def delete_pack(pack_id: str) -> bool:
    """删记录并删源文件（源文件不删则重新生成的基底会越积越多）。"""
    with _lock:
        rows = _all()
        entry = next((p for p in rows if p.get("id") == pack_id), None)
        if entry is None:
            return False
        _save_all([p for p in rows if p.get("id") != pack_id])
    if entry.get("source_path"):
        try:
            _source_abs_path(entry["source_path"]).unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001 — 文件被占用不阻塞记录删除
            logger.warning("主题包源文件删除失败：%s", exc)
    return True


def source_bytes(pack: dict) -> bytes:
    path = _source_abs_path(pack.get("source_path", ""))
    if not path.is_file():
        return b""
    return path.read_bytes()


def style_of(pack: dict) -> dict:
    """主题包 → {"preset", "report"}。本次改动之前上传的包没有 style，就地补抽并落库。"""
    style = pack.get("style") or {}
    if (style.get("report") or {}).get("version") == template_style.STYLE_VERSION and style.get("preset"):
        return style
    meta = pack.get("meta") or {}
    slide_size = meta.get("slide_size") or {}
    palette = pack.get("palette") or {}
    fonts = pack.get("fonts") or {}
    raw = source_bytes(pack)
    if raw:
        try:
            prs = Presentation(io.BytesIO(raw))
            style = template_style.extract(
                prs, palette=palette, fonts=fonts, layouts=pack.get("layouts") or [],
                slide_size=slide_size, name=pack.get("name", ""), pack_id=pack.get("id", ""),
            )
        except Exception:  # noqa: BLE001 — 源文件损坏也要能生成，退到主题色板
            logger.exception("模板风格补抽失败 pack=%s", pack.get("id"))
            style = template_style.fallback(palette, fonts, slide_size, name=pack.get("name", ""))
    else:
        style = template_style.fallback(palette, fonts, slide_size, name=pack.get("name", ""))
    if pack.get("id"):
        _persist_style(pack["id"], style)
    pack["style"] = style  # 调用方（生成链路）随后要把适配说明并进保真度报告
    return style


def _persist_style(pack_id: str, style: dict) -> None:
    with _lock:
        rows = _all()
        entry = next((p for p in rows if p.get("id") == pack_id), None)
        if entry is None:
            return
        entry["style"] = style
        entry["updated_at"] = _now()
        _save_all(rows)


def preset_for(theme_pack_id: str, preset_theme: str, workspace_id: str, *,
               outline=None, session_id: str = "") -> dict:
    """风格 id → preset。内置查表，上传包走抽取；不可见/已删按默认风格继续。

    生成链路要区分「模板不见了」并报错，预览不该因为模板被删就整块不可用，
    所以这里的兜底是给预览用的，生成侧仍走 generator 里的显式校验。

    没选风格（或选了「智能方向」）时，按大纲内容现生成一套方向——预览与生成
    都从这里取，读的是同一份缓存，所见即所得。
    """
    builtin = designs.resolve_id(theme_pack_id)
    if builtin is not None and not designs.wants_auto(theme_pack_id, preset_theme):
        return builtin
    if theme_pack_id and not designs.wants_auto(theme_pack_id, preset_theme):
        pack = get_visible_pack(theme_pack_id, workspace_id)
        if pack is not None:
            return style_of(pack)["preset"]
    if outline is not None and designs.wants_auto(theme_pack_id, preset_theme):
        from app.services.ppt_studio.htmlkit import direction

        return direction.resolve_for(outline, session_id, workspace_id).preset()
    return designs.resolve_theme(preset_theme) or designs.default_preset()


def family_payload(pack: dict) -> dict:
    """上传模板 → 设计家族卡（与 tokens.all_families 同形，设计台直接混排）。"""
    style = style_of(pack)
    preset, report = style["preset"], style["report"]
    palette, family = preset["palette"], preset["family"]
    return {
        "id": pack.get("id", ""),
        "key": family["key"],
        "kind": "template",
        "name": pack.get("name", "公司模板"),
        "tagline": report.get("summary_zh", ""),
        "mode": family["mode"],
        "motif": family["motif"],
        "swatches": [palette["bg"], palette["accent"], palette["accent2"], palette["paper"]],
        "chart": list(family["chart"]),
        "ratio": report.get("ratio", ""),
        "notes": list(report.get("notes") or []),
        "fonts": report.get("fonts") or {},
        "radius": int(report.get("radius_px", 14)),
        "margin_px": int(report.get("margin_px", 64)),
    }


def can_edit(pack: dict, owner_id: str, is_admin: bool = False) -> bool:
    """删除权限：仅 owner 本人或 admin（workspace 共享只给读）。"""
    return pack.get("owner_id") == owner_id or is_admin


def swatches(palette: dict) -> list[str]:
    return [str(palette.get(key, "")) for key in ("accent1", "accent2", "accent3", "accent4", "accent5")]


def list_payload(pack: dict) -> dict:
    """列表项载荷：色板缩略 + 保真度 + 版式数，不含全量 layouts。"""
    # 023 之前入库的包没有 style（那时表里没这列），列表不能因此退回主题 accent：
    # 就地补抽一次并落库，之后走缓存
    if not (pack.get("style") or {}).get("report"):
        style_of(pack)
    report = (pack.get("style") or {}).get("report") or {}
    # 抽出来的品牌色比主题 accent1 更接近模板实际观感（很多模板压根没改主题）
    swatch = [report.get("background"), report.get("accent"), report.get("accent2")]
    swatch = [s for s in swatch if s] or swatches(pack.get("palette") or {})
    return {
        "id": pack.get("id", ""),
        "kind": "upload",
        "name": pack.get("name", ""),
        "tagline": report.get("summary_zh", ""),
        "owner_id": pack.get("owner_id", ""),
        "status": pack.get("status", "ready"),
        "layout_count": len(pack.get("layouts") or []),
        "fidelity_score": int((pack.get("fidelity") or {}).get("score", 0)),
        "swatches": swatch,
        "mode": report.get("mode", ""),
        "ratio": report.get("ratio", ""),
        "created_at": pack.get("created_at", ""),
    }


def detail_payload(pack: dict) -> dict:
    """详情载荷（全量 layouts），TemplatePicker 与生成预检共用。"""
    if not (pack.get("style") or {}).get("report"):
        style_of(pack)
    meta = pack.get("meta") or {}
    fidelity = pack.get("fidelity") or {}
    return {
        "id": pack.get("id", ""),
        "name": pack.get("name", ""),
        "owner_id": pack.get("owner_id", ""),
        "status": pack.get("status", "ready"),
        "source_filename": pack.get("source_filename", ""),
        "created_at": pack.get("created_at", ""),
        "slide_size": meta.get("slide_size", {"width_in": 13.333, "height_in": 7.5}),
        "palette": pack.get("palette") or {},
        "fonts": pack.get("fonts") or {},
        "layouts": pack.get("layouts") or [],
        "style": (pack.get("style") or {}).get("report") or {},
        "header_footer": meta.get("header_footer") or {"has_slide_number": False, "has_date": False, "snippets": []},
        "fidelity": {
            "score": int(fidelity.get("score", 100)),
            "issues": fidelity.get("issues") or [],
            "degradations": fidelity.get("degradations") or [],
            "summary_zh": fidelity.get("summary_zh", ""),
        },
    }


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
