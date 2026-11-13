"""实测几何 → 原生 PPTX 形状。

**为什么不是贴图**：贴一张 1280×720 的 PNG 最省事，但用户拿到的是不可编辑的
图片，改一个字都要回炉。这里把 Chromium 量到的每个 DOM 矩形写成原生
AutoShape、每个文本行写成原生 TextBox，所以在 PowerPoint 等兼容编辑器里
可以直接选中标题改字、拖动卡片、换填充色。

**为什么按「行」而不是按「块」建文本框**：块级文本框交给 PowerPoint 自己折行，
它的字距/断行规则与 Chromium 不同，一行差一个字整页就塌。这里让浏览器先折好行，
每行一个文本框（行高 = 实测行高，垂直居中，关闭自动换行），位置就锁死了；
同一个文本块的行放进一个 GroupShape，选中/移动仍是整块，编辑体验不受影响。

坐标口径：画布 1280×720 px == 13.333in × 7.5in（96 dpi），px→EMU ×9525，
px→pt ×0.75。所有几何都来自 layout.json，这里不做任何「估算行数」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, qn
from pptx.util import Emu, Pt

from app.core.logging import get_logger
from app.services.ppt_studio.htmlkit import color, icon_geom
from app.services.ppt_studio.htmlkit.tokens import EMU_PER_PX, PT_PER_PX

logger = get_logger(__name__)

# 中日韩字体：这些字族要写进 a:ea / a:cs，否则 PowerPoint 会拿主题默认宋体兜底，
# 中文行宽立刻和浏览器对不上。
_CJK_FONTS = (
    "Microsoft YaHei", "微软雅黑", "Noto Sans SC", "Noto Serif SC", "DengXian", "等线",
    "SimSun", "宋体", "SimHei", "黑体", "Source Han", "PingFang", "Hiragino",
)
_GENERIC = {"sans-serif", "serif", "monospace", "system-ui", "inherit", "initial"}
# Chromium 序列化时会省略默认方向：`linear-gradient(180deg, a, b)` 读回来是
# `linear-gradient(a 0%, b 100%)`。角度必须可选，否则柱状图的竖向渐变整根消失。
_GRADIENT = re.compile(r"linear-gradient\(\s*(?:([\d.]+)deg\s*,\s*)?(.+)\)\s*$", re.S)
_STOP = re.compile(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))\s+([\d.]+)%")


def px(value: float) -> Emu:
    return Emu(int(round(float(value) * EMU_PER_PX)))


def _rgb(hexed: str) -> RGBColor:
    return RGBColor.from_string(hexed)


def _font_stack(stack: str) -> tuple[str, str]:
    """CSS font-family 栈 → (拉丁字体, 中文字体)。"""
    names = [part.strip().strip('"').strip("'") for part in (stack or "").split(",")]
    names = [name for name in names if name and name.lower() not in _GENERIC]
    if not names:
        return "Microsoft YaHei", "Microsoft YaHei"
    latin = names[0]
    cjk = next((name for name in names if any(key in name for key in _CJK_FONTS)), latin)
    return latin, cjk


def _set_run_font(run, family: str, *, bold: bool, italic: bool, size_px: float,
                  hexed: str, alpha: float, spacing_px: float) -> None:
    font = run.font
    font.size = Pt(round(size_px * PT_PER_PX, 1))
    font.bold = bold
    font.italic = italic
    font.color.rgb = _rgb(hexed)
    latin, cjk = _font_stack(family)
    font.name = latin
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        node = rpr.find(qn(tag))
        if node is None:
            node = rpr.makeelement(qn(tag), {})
            rpr.append(node)
        node.set("typeface", cjk)
    if abs(spacing_px) > 0.01:
        rpr.set("spc", str(int(round(spacing_px * PT_PER_PX * 100))))
    if alpha < 1:
        fill = rpr.find(qn("a:solidFill"))
        if fill is not None:
            clr = fill.find(qn("a:srgbClr"))
            if clr is not None:
                node = clr.makeelement(qn("a:alpha"), {"val": str(int(round(alpha * 100000)))})
                clr.append(node)


def _solid_fill(fill, hexed: str, alpha: float) -> None:
    fill.solid()
    fill.fore_color.rgb = _rgb(hexed)
    if alpha < 1:
        clr = fill.fore_color._xFill.find(qn("a:srgbClr"))
        if clr is not None:
            clr.append(clr.makeelement(qn("a:alpha"), {"val": str(int(round(alpha * 100000)))}))


def _split_stops(body: str) -> list[str]:
    """按顶层逗号切色标——rgba(...) 里的逗号不能算。"""
    parts, depth, current = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def _gradient_fill(shape, css: str, opacity: float) -> bool:
    """CSS linear-gradient → a:gradFill。只认「（可选角度 +）百分比色标」这一种写法。"""
    match = _GRADIENT.match(css or "")
    if not match:
        return False
    stops = _STOP.findall(match.group(2))
    if len(stops) < 2:
        # 没写位置的色标（`linear-gradient(#a, #b)`）浏览器也会原样吐回来，均分补位
        bare = [part.strip() for part in _split_stops(match.group(2))]
        bare = [part for part in bare if color.parse(part)[1] > 0]
        if len(bare) < 2:
            return False
        stops = [(part, str(i * 100 / (len(bare) - 1))) for i, part in enumerate(bare)]
    # CSS 0deg 向上、顺时针；OOXML ang 从三点钟方向起顺时针，单位 1/60000 度
    angle = (float(match.group(1) or 180.0) - 90.0) % 360.0
    sp_pr = shape._element.spPr
    for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill", "a:grpFill"):
        node = sp_pr.find(qn(tag))
        if node is not None:
            sp_pr.remove(node)
    grad = sp_pr.makeelement(qn("a:gradFill"), {"flip": "none", "rotWithShape": "1"})
    gs_lst = grad.makeelement(qn("a:gsLst"), {})
    for raw, pos in stops:
        hexed, alpha = color.parse(raw)
        gs = gs_lst.makeelement(qn("a:gs"), {"pos": str(int(round(float(pos) * 1000)))})
        clr = gs.makeelement(qn("a:srgbClr"), {"val": hexed})
        effective = alpha * opacity
        if effective < 1:
            clr.append(clr.makeelement(qn("a:alpha"), {"val": str(int(round(effective * 100000)))}))
        gs.append(clr)
        gs_lst.append(gs)
    grad.append(gs_lst)
    grad.append(grad.makeelement(qn("a:lin"), {"ang": str(int(round(angle * 60000))), "scaled": "0"}))
    # 插入位置必须符合 CT_ShapeProperties 的顺序：几何 → 填充 → 线条 → 效果。
    # 追加到末尾（effectLst 之后）PowerPoint 会当作非法内容忽略，转而用主题
    # 默认填充——表现就是整页变成 Office 默认浅蓝渐变。
    geometry = sp_pr.find(qn("a:prstGeom"))
    if geometry is None:
        geometry = sp_pr.find(qn("a:custGeom"))
    index = list(sp_pr).index(geometry) + 1 if geometry is not None else 0
    sp_pr.insert(index, grad)
    return True


def _strip_theme_style(shape) -> None:
    """去掉 python-pptx 默认挂上的 <p:style>。

    它引用主题里的填充/线条/效果，换个模板（用户自己的母版）
    就会变样。所有视觉属性这里都显式写死，不依赖任何主题。
    """
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def _corner_shape(radii: list[float], w: float, h: float) -> tuple[MSO_SHAPE, list[float]]:
    """圆角 → 预设几何 + 调整值。roundRect 的 adj 是「半径 / 短边」，上限 0.5。"""
    short = max(1.0, min(w, h))
    top_left, top_right, bottom_right, bottom_left = [max(0.0, r) for r in radii]
    if max(radii) <= 0.4:
        return MSO_SHAPE.RECTANGLE, []
    same_top = abs(top_left - top_right) < 0.6
    same_bottom = abs(bottom_left - bottom_right) < 0.6
    if same_top and same_bottom and abs(top_left - bottom_left) > 0.6:
        # round2SameRect：上下两组圆角可分别给（柱状图顶部圆角就靠它）
        return MSO_SHAPE.ROUND_2_SAME_RECTANGLE, [
            min(0.5, top_left / short), min(0.5, bottom_left / short)
        ]
    return MSO_SHAPE.ROUNDED_RECTANGLE, [min(0.5, max(radii) / short)]


def _apply_border(shape, style: dict, rect: dict, opacity: float) -> list[dict]:
    """四边等宽描边直接给形状；只有单边有描边就补一条细矩形（更准，也更好编辑）。"""
    widths = style.get("borderWidth") or [0, 0, 0, 0]
    colors = style.get("borderColor") or [""] * 4
    styles = style.get("borderStyle") or ["none"] * 4
    active = [
        i for i in range(4)
        if widths[i] > 0.2 and styles[i] not in ("none", "hidden") and color.parse(colors[i])[1] > 0
    ]
    if not active:
        shape.line.fill.background()
        return []
    uniform = (
        len(active) == 4
        and max(widths) - min(widths) < 0.2
        and len({color.parse(colors[i])[0] for i in active}) == 1
    )
    if uniform:
        hexed, alpha = color.parse(colors[active[0]])
        shape.line.color.rgb = _rgb(hexed)
        shape.line.width = Pt(round(widths[active[0]] * PT_PER_PX, 2))
        if alpha * opacity < 1:
            ln = shape._element.spPr.find(qn("a:ln"))
            clr = ln.find(qn("a:solidFill")).find(qn("a:srgbClr")) if ln is not None else None
            if clr is not None:
                clr.append(clr.makeelement(qn("a:alpha"), {"val": str(int(round(alpha * opacity * 100000)))}))
        return []
    shape.line.fill.background()
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    edges = {0: (x, y, w, widths[0]), 1: (x + w - widths[1], y, widths[1], h),
             2: (x, y + h - widths[2], w, widths[2]), 3: (x, y, widths[3], h)}
    return [
        {"rect": dict(zip(("x", "y", "w", "h"), edges[i])), "fill": colors[i], "alpha": opacity}
        for i in active
    ]


def _add_rect(shapes, rect: dict, style: dict, *, name: str = ""):
    """一个 DOM 盒子 → 一个原生形状（含渐变 / 圆角 / 描边 / 透明度）。"""
    w, h = float(rect["w"]), float(rect["h"])
    if w <= 0.2 or h <= 0.2:
        return None
    opacity = float(style.get("opacity", 1) or 0)
    if opacity <= 0.01:
        return None
    bg_hex, bg_alpha = color.parse(style.get("bg") or "")
    gradient = style.get("bgImage") or "none"
    has_gradient = gradient != "none" and _GRADIENT.match(gradient) is not None
    widths = style.get("borderWidth") or [0, 0, 0, 0]
    has_border = any(
        widths[i] > 0.2 and (style.get("borderStyle") or ["none"] * 4)[i] not in ("none", "hidden")
        for i in range(4)
    )
    if bg_alpha <= 0 and not has_gradient and not has_border:
        return None
    kind, adjustments = _corner_shape(style.get("radius") or [0, 0, 0, 0], w, h)
    shape = shapes.add_shape(kind, px(rect["x"]), px(rect["y"]), px(w), px(h))
    _strip_theme_style(shape)
    if name:
        shape.name = name
    for index, value in enumerate(adjustments):
        try:
            shape.adjustments[index] = value
        except (IndexError, ValueError):
            break
    shape.shadow.inherit = False
    if has_gradient and _gradient_fill(shape, gradient, opacity):
        pass
    elif bg_alpha > 0:
        _solid_fill(shape.fill, bg_hex, bg_alpha * opacity)
    else:
        shape.fill.background()
    extras = _apply_border(shape, style, rect, opacity)
    for extra in extras:
        edge = shapes.add_shape(
            MSO_SHAPE.RECTANGLE, px(extra["rect"]["x"]), px(extra["rect"]["y"]),
            px(extra["rect"]["w"]), px(extra["rect"]["h"]),
        )
        _strip_theme_style(edge)
        edge.shadow.inherit = False
        hexed, alpha = color.parse(extra["fill"])
        _solid_fill(edge.fill, hexed, alpha * extra["alpha"])
        edge.line.fill.background()
    # 预设形状自带主题文字样式，清空避免残留占位文本
    shape.text_frame.word_wrap = False
    return shape


def _add_text(shapes, node: dict) -> None:
    """一个文本块 → 一个 Group，块内每行一个 TextBox（行位置由浏览器锁定）。"""
    lines = [line for line in (node.get("lines") or []) if (line.get("runs") or [])]
    if not lines:
        return
    style = node.get("style") or {}
    opacity = float(style.get("opacity", 1) or 0)
    if opacity <= 0.01:
        return
    group = shapes.add_group_shape()
    group.name = f"text:{node.get('className') or node.get('id')}"
    for line in lines:
        width = max(4.0, float(line["w"]) + 6.0)
        height = max(6.0, float(line["h"]))
        box = group.shapes.add_textbox(px(line["x"]), px(line["y"]), px(width), px(height))
        box.name = "line"
        frame = box.text_frame
        frame.word_wrap = False
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
        paragraph = frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.LEFT
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(0)
        paragraph.line_spacing = Pt(round(height * PT_PER_PX, 2))
        for raw in line["runs"]:
            text = raw.get("text") or ""
            if not text:
                continue
            run = paragraph.add_run()
            run.text = text
            hexed, alpha = color.parse(raw.get("color") or style.get("color") or "")
            if alpha <= 0:
                continue
            weight = raw.get("fontWeight") or style.get("fontWeight") or "400"
            try:
                bold = float(weight) >= 600
            except (TypeError, ValueError):
                bold = str(weight) in ("bold", "bolder")
            _set_run_font(
                run,
                raw.get("fontFamily") or style.get("fontFamily") or "",
                bold=bold,
                italic=(raw.get("fontStyle") or "normal") == "italic",
                size_px=float(raw.get("fontSize") or style.get("fontSize") or 23),
                hexed=hexed,
                alpha=alpha * opacity,
                spacing_px=float(raw.get("letterSpacing") or 0),
            )
    if not group.shapes:
        shapes._spTree.remove(group._element)


def _pie_arc(shapes, rect: dict, start: float, end: float, hexed: str, alpha: float) -> None:
    shape = shapes.add_shape(
        MSO_SHAPE.PIE, px(rect["x"]), px(rect["y"]), px(rect["w"]), px(rect["h"])
    )
    shape.name = "donut-segment"
    _strip_theme_style(shape)
    shape.shadow.inherit = False
    # pie 预设的 adj 是 1/60000 度，python-pptx 的 adjustments 按 1/100000 归一
    try:
        shape.adjustments[0] = start * 0.6
        shape.adjustments[1] = end * 0.6
    except (IndexError, ValueError):
        pass
    _solid_fill(shape.fill, hexed, alpha)
    shape.line.fill.background()


def _add_icon(shapes, node: dict) -> None:
    """图标指令 → 自定义几何 + 描边。

    用 custGeom 而不是图标字体：字体缺一个字符就是豆腐块，还挡不住用户改字号；
    路径形状在任何机器上都一样，也能单独选中、换色、缩放。
    """
    data = node.get("data") or {}
    try:
        nodes = json.loads(data.get("nodes") or "[]")
    except json.JSONDecodeError:
        return
    rect = node.get("rect") or {}
    if not nodes or rect.get("w", 0) <= 0 or rect.get("h", 0) <= 0:
        return
    box = float(data.get("vb") or 24) or 24.0
    hexed, alpha = color.parse("#" + str(data.get("color") or "4F6BF6").lstrip("#"))
    commands = icon_geom.path_commands(nodes, box)
    if not commands:
        return

    shape = shapes.add_shape(
        MSO_SHAPE.RECTANGLE, px(rect["x"]), px(rect["y"]), px(rect["w"]), px(rect["h"])
    )
    shape.name = "icon"
    sp_pr = shape._element.spPr
    prst = sp_pr.find(qn("a:prstGeom"))
    if prst is not None:
        sp_pr.remove(prst)
    grid = int(icon_geom.GRID)
    sp_pr.insert(1, parse_xml(
        f'<a:custGeom {nsdecls("a")}><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
        f'<a:rect l="0" t="0" r="r" b="b"/><a:pathLst>'
        f'<a:path w="{grid}" h="{grid}" fill="none">{commands}</a:path>'
        f'</a:pathLst></a:custGeom>'
    ))
    shape.shadow.inherit = False
    shape.fill.background()
    line = shape.line
    line.color.rgb = RGBColor.from_string(hexed)
    line.width = Pt(float(data.get("stroke") or 2) * (rect["w"] / box) * PT_PER_PX)
    ln = sp_pr.find(qn("a:ln"))
    if ln is not None:
        ln.set("cap", "rnd")
        ln.append(parse_xml(f'<a:round {nsdecls("a")}/>'))


def _add_spark(shapes, node: dict) -> None:
    """折线指令 → 原生直线连接符（外加数据点圆点）。

    为什么不用原生 chart 对象：一张 pptx 图表在 PowerPoint 里只能整体改数据，
    而这里每个顶点都要能单独拖；用连接符画出来的线是普通形状，编辑自由度和
    柱状图一致，也不依赖 Excel 数据源。
    """
    data = node.get("data") or {}
    try:
        points = json.loads(data.get("points") or "[]")
    except json.JSONDecodeError:
        return
    if len(points) < 2:
        return
    vw = float(data.get("vw") or 1000) or 1000.0
    vh = float(data.get("vh") or 300) or 300.0
    rect = node["rect"]
    hexed, alpha = color.parse("#" + str(data.get("color") or "4F6BF6"))
    width = float(data.get("stroke") or 3)

    def place(point) -> tuple[float, float]:
        return (rect["x"] + float(point[0]) / vw * rect["w"],
                rect["y"] + float(point[1]) / vh * rect["h"])

    mapped = [place(point) for point in points]
    for (x1, y1), (x2, y2) in zip(mapped, mapped[1:]):
        conn = shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, px(x1), px(y1), px(x2), px(y2)
        )
        conn.line.color.rgb = RGBColor.from_string(hexed)
        conn.line.width = Pt(width * PT_PER_PX)

    if str(data.get("dots") or "") not in ("", "0", "false"):
        radius = float(data.get("radius") or 6)
        for x, y in mapped:
            dot = shapes.add_shape(
                MSO_SHAPE.OVAL, px(x - radius), px(y - radius), px(radius * 2), px(radius * 2)
            )
            dot.name = "spark-dot"
            dot.shadow.inherit = False
            dot.fill.solid()
            dot.fill.fore_color.rgb = RGBColor.from_string(hexed)
            dot.line.fill.background()


def _add_donut(shapes, node: dict) -> None:
    """环形图指令 → 原生 PIE 扇形 + 中心 OVAL 挖洞（和 SVG 画法一一对应）。

    坑：PowerPoint 的 pie 预设把角度钳在 0~360°，写 421° 会被截成 360°，
    扇形就少一截。所以跨越 0° 的扇形必须拆成两段画。
    """
    data = node.get("data") or {}
    try:
        segments = json.loads(data.get("segments") or "[]")
    except json.JSONDecodeError:
        return
    rect = node["rect"]
    hole = max(0.0, min(0.92, float(data.get("hole") or 0.6)))
    for segment in segments:
        raw_start = float(segment.get("start") or 0)
        sweep = float(segment.get("end") or 0) - raw_start
        if sweep <= 0.01:
            continue
        hexed, alpha = color.parse("#" + str(segment.get("fill") or "888888"))
        if sweep >= 359.99:
            oval = shapes.add_shape(
                MSO_SHAPE.OVAL, px(rect["x"]), px(rect["y"]), px(rect["w"]), px(rect["h"])
            )
            oval.name = "donut-full"
            _strip_theme_style(oval)
            oval.shadow.inherit = False
            _solid_fill(oval.fill, hexed, alpha)
            oval.line.fill.background()
            continue
        start = raw_start % 360.0
        remaining = sweep
        while remaining > 0.01:
            span = min(remaining, 360.0 - start)
            _pie_arc(shapes, rect, start, start + span, hexed, alpha)
            remaining -= span
            start = 0.0
    if hole <= 0.01:
        return
    inset_x = rect["w"] * (1 - hole) / 2
    inset_y = rect["h"] * (1 - hole) / 2
    oval = shapes.add_shape(
        MSO_SHAPE.OVAL, px(rect["x"] + inset_x), px(rect["y"] + inset_y),
        px(rect["w"] * hole), px(rect["h"] * hole),
    )
    oval.name = "donut-hole"
    _strip_theme_style(oval)
    oval.shadow.inherit = False
    hexed, alpha = color.parse("#" + str(data.get("hole-fill") or "FFFFFF"))
    _solid_fill(oval.fill, hexed, alpha)
    oval.line.fill.background()


def _background(slide, node: dict | None, tokens: dict) -> None:
    """整页底色单独铺一层满版矩形：渐变封面也能 1:1 还原。"""
    rect = {"x": 0, "y": 0, "w": tokens["canvas_w"], "h": tokens["canvas_h"]}
    style = dict((node or {}).get("style") or {})
    style.setdefault("bg", "#" + tokens["colors"]["bg"])
    style["radius"] = [0, 0, 0, 0]
    style["borderWidth"] = [0, 0, 0, 0]
    style["opacity"] = 1
    shape = _add_rect(slide.shapes, rect, style, name="background")
    if shape is not None:
        shape.text_frame.text = ""


def write_slide(slide, measured: dict, tokens: dict) -> dict:
    """把一页实测结果写进 slide。返回形状统计，供 fidelity 汇报。"""
    nodes = measured.get("nodes") or []
    root = next((n for n in nodes if n.get("id") == "root"), None)
    _background(slide, root, tokens)
    counts = {"shapes": 0, "text_blocks": 0, "text_lines": 0, "directives": 0}
    for node in nodes:
        if node.get("id") == "root":
            continue
        kind = node.get("kind")
        if kind == "directive":
            if node.get("directive") == "donut":
                _add_donut(slide.shapes, node)
                counts["directives"] += 1
            elif node.get("directive") == "spark":
                _add_spark(slide.shapes, node)
                counts["directives"] += 1
            elif node.get("directive") == "icon":
                _add_icon(slide.shapes, node)
                counts["directives"] += 1
            continue
        if kind == "text":
            _add_text(slide.shapes, node)
            counts["text_blocks"] += 1
            counts["text_lines"] += len(node.get("lines") or [])
            continue
        if _add_rect(slide.shapes, node["rect"], node.get("style") or {},
                     name=node.get("className") or "box") is not None:
            counts["shapes"] += 1
    return counts


def write_deck(measured: list[dict], tokens: dict, out_path: Path, *, notes: list[str] | None = None) -> dict:
    """整份实测结果 → .pptx。返回 {path, total_slides, shapes, text_lines, layouts_used}。"""
    presentation = Presentation()
    presentation.slide_width = px(tokens["canvas_w"])
    presentation.slide_height = px(tokens["canvas_h"])
    blank = presentation.slide_layouts[6]
    totals = {"shapes": 0, "text_blocks": 0, "text_lines": 0, "directives": 0}
    for position, page in enumerate(measured):
        slide = presentation.slides.add_slide(blank)
        counts = write_slide(slide, page, tokens)
        for key, value in counts.items():
            totals[key] += value
        note = (notes[position] if notes and position < len(notes) else "") or ""
        if note:
            slide.notes_slide.notes_text_frame.text = note
    out_path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(out_path))
    logger.info("原生 PPTX 写出：%d 页 / %d 形状 / %d 文本行 → %s",
                len(measured), totals["shapes"], totals["text_lines"], out_path)
    return {"path": str(out_path), "total_slides": len(measured), **totals}


__all__ = ["write_deck", "write_slide", "px"]