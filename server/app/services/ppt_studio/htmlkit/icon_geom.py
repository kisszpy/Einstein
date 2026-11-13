"""Lucide 线性图标 → DrawingML 自定义几何。

为什么不用图标字体：PPT 里图标字体是文本 run，观看端没装就显示豆腐块，
python-pptx 也不支持嵌字体。这里把 SVG 路径转成 `a:custGeom` + 描边，
落在 pptx 里是普通可编辑形状，不依赖任何字体，放大不糊。

支持 Lucide 用到的全部写法：M/L/H/V/C/A/Z（含相对指令）。圆弧按端点参数
转中心参数再拆成 ≤90° 的三次贝塞尔——DrawingML 没有弧指令。
"""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"[MmLlHhVvCcAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
# 曲线用 10 万分之一栅格，和形状尺寸解耦：同一份几何任意缩放都不失真
GRID = 100000.0


def path_commands(nodes: list, viewbox: float = 24.0) -> str:
    """图标的元素列表 → a:path 内部的命令串（坐标已映射到 GRID）。"""
    subpaths: list[str] = []
    for tag, attrs in nodes:
        if tag == "path":
            subpaths.append(_from_path_d(attrs.get("d") or "", viewbox))
        elif tag == "circle":
            cx, cy, r = _num(attrs, "cx"), _num(attrs, "cy"), _num(attrs, "r")
            subpaths.append(_ellipse(cx, cy, r, r, viewbox))
        elif tag == "ellipse":
            subpaths.append(_ellipse(_num(attrs, "cx"), _num(attrs, "cy"),
                                     _num(attrs, "rx"), _num(attrs, "ry"), viewbox))
        elif tag == "line":
            x1, y1 = _num(attrs, "x1"), _num(attrs, "y1")
            x2, y2 = _num(attrs, "x2"), _num(attrs, "y2")
            subpaths.append(
                f'<a:moveTo><a:pt {_pt(x1, y1, viewbox)}/></a:moveTo>'
                f'<a:lnTo><a:pt {_pt(x2, y2, viewbox)}/></a:lnTo>'
            )
        elif tag == "rect":
            subpaths.append(_rect(attrs, viewbox))
        elif tag in ("polyline", "polygon"):
            points = _points(attrs.get("points") or "", viewbox)
            if not points:
                continue
            body = f'<a:moveTo><a:pt {points[0]}/></a:moveTo>'
            body += "".join(f'<a:lnTo><a:pt {point}/></a:lnTo>' for point in points[1:])
            if tag == "polygon":
                body += "<a:close/>"
            subpaths.append(body)
    return "".join(subpaths)


# ---------------------------------------------------------------- 基本图元


def _num(attrs: dict, key: str) -> float:
    try:
        return float(attrs.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _pt(x: float, y: float, viewbox: float) -> str:
    return f'x="{int(round(x / viewbox * GRID))}" y="{int(round(y / viewbox * GRID))}"'


def _points(text: str, viewbox: float) -> list[str]:
    numbers = [float(value) for value in re.findall(r"-?(?:\d+\.?\d*|\.\d+)", text or "")]
    return [_pt(numbers[i], numbers[i + 1], viewbox) for i in range(0, len(numbers) - 1, 2)]


def _ellipse(cx: float, cy: float, rx: float, ry: float, viewbox: float) -> str:
    """整圆/椭圆：拆成四段三次贝塞尔，比两段稳一些。"""
    k = 0.5523
    segments = [
        (cx + rx, cy, cx + rx, cy + ry * k, cx + rx * k, cy + ry, cx, cy + ry),
        (cx, cy + ry, cx - rx * k, cy + ry, cx - rx, cy + ry * k, cx - rx, cy),
        (cx - rx, cy, cx - rx, cy - ry * k, cx - rx * k, cy - ry, cx, cy - ry),
        (cx, cy - ry, cx + rx * k, cy - ry, cx + rx, cy - ry * k, cx + rx, cy),
    ]
    body = f'<a:moveTo><a:pt {_pt(cx + rx, cy, viewbox)}/></a:moveTo>'
    for _, _, c1x, c1y, c2x, c2y, x, y in segments:
        body += (
            f'<a:cubicBezTo><a:pt {_pt(c1x, c1y, viewbox)}/>'
            f'<a:pt {_pt(c2x, c2y, viewbox)}/><a:pt {_pt(x, y, viewbox)}/></a:cubicBezTo>'
        )
    return body + "<a:close/>"


def _rect(attrs: dict, viewbox: float) -> str:
    x, y = _num(attrs, "x"), _num(attrs, "y")
    w, h = _num(attrs, "width"), _num(attrs, "height")
    r = min(_num(attrs, "rx"), w / 2, h / 2)
    if r <= 0:
        return (
            f'<a:moveTo><a:pt {_pt(x, y, viewbox)}/></a:moveTo>'
            f'<a:lnTo><a:pt {_pt(x + w, y, viewbox)}/></a:lnTo>'
            f'<a:lnTo><a:pt {_pt(x + w, y + h, viewbox)}/></a:lnTo>'
            f'<a:lnTo><a:pt {_pt(x, y + h, viewbox)}/></a:lnTo><a:close/>'
        )
    k = 0.5523 * r
    parts = [
        (x + r, y, "move"),
        (x + w - r, y, "line"),
        (x + w - r + k, y, "cub1"), (x + w, y + r - k, "cub2"), (x + w, y + r, "cub3"),
        (x + w, y + h - r, "line"),
        (x + w, y + h - r + k, "cub1"), (x + w - r + k, y + h, "cub2"), (x + w - r, y + h, "cub3"),
        (x + r, y + h, "line"),
        (x + r - k, y + h, "cub1"), (x, y + h - r + k, "cub2"), (x, y + h - r, "cub3"),
        (x, y + r, "line"),
        (x, y + r - k, "cub1"), (x + r - k, y, "cub2"), (x + r, y, "cub3"),
    ]
    body = ""
    pending: list[str] = []
    for px_, py_, kind in parts:
        point = _pt(px_, py_, viewbox)
        if kind == "move":
            body += f"<a:moveTo><a:pt {point}/></a:moveTo>"
        elif kind == "line":
            body += f"<a:lnTo><a:pt {point}/></a:lnTo>"
        else:
            pending.append(point)
            if len(pending) == 3:
                body += "<a:cubicBezTo>" + "".join(f"<a:pt {p}/>" for p in pending) + "</a:cubicBezTo>"
                pending = []
    return body + "<a:close/>"


# ---------------------------------------------------------------- 路径解析


# 圆弧的两个标志位常和坐标连写（`0 005.5` = rot 0、flag 0、flag 0、x 5.5），
# 先按字符拆开，后面的分词器才能正确切分数值。
_PACKED_FLAGS = re.compile(r"(?<=\s)([01])([01])(?=[-+.\d])")


_NUMBER_AT = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _from_path_d(d: str, viewbox: float) -> str:
    """按字符扫描路径。

    不能用「先分词再读 7 个参数」：SVG 的弧标志位可以连写、并且会和坐标黏在
    一起（`0 0018.5` = rot 0、flag 0、flag 0、x 18.5），任何全局分词都会把
    `10.5` 这种坐标误拆成两个标志位。标志位只能按字符读。
    """
    text = d or ""
    length = len(text)
    index = 0
    out: list[str] = []
    cx = cy = sx = sy = 0.0
    cmd = ""

    def skip() -> None:
        nonlocal index
        while index < length and text[index].isspace():
            index += 1

    def number() -> float:
        nonlocal index
        skip()
        match = _NUMBER_AT.match(text, index)
        if not match:
            raise ValueError(f"路径里缺少数值：{text[max(0, index - 8):index + 8]}")
        index = match.end()
        return float(match.group(0))

    def flag() -> bool:
        nonlocal index
        skip()
        if index >= length or text[index] not in "01":
            raise ValueError(f"圆弧标志位不合法：{text[max(0, index - 8):index + 8]}")
        index += 1
        return text[index - 1] == "1"

    while True:
        skip()
        if index >= length:
            break
        if text[index].isalpha():
            cmd = text[index]
            index += 1
            if cmd in "Zz":
                out.append("<a:close/>")
                cx, cy = sx, sy
                continue
        elif not cmd:
            break

        if cmd in "Mm":
            x, y = number(), number()
            if cmd == "m":
                x, y = cx + x, cy + y
            out.append(f'<a:moveTo><a:pt {_pt(x, y, viewbox)}/></a:moveTo>')
            cx, cy = sx, sy = x, y
            cmd = "l" if cmd == "m" else "L"
        elif cmd in "Ll":
            x, y = number(), number()
            if cmd == "l":
                x, y = cx + x, cy + y
            out.append(f'<a:lnTo><a:pt {_pt(x, y, viewbox)}/></a:lnTo>')
            cx, cy = x, y
        elif cmd in "Hh":
            x = number()
            if cmd == "h":
                x += cx
            out.append(f'<a:lnTo><a:pt {_pt(x, cy, viewbox)}/></a:lnTo>')
            cx = x
        elif cmd in "Vv":
            y = number()
            if cmd == "v":
                y += cy
            out.append(f'<a:lnTo><a:pt {_pt(cx, y, viewbox)}/></a:lnTo>')
            cy = y
        elif cmd in "Cc":
            values = [number() for _ in range(6)]
            if cmd == "c":
                values = [values[k] + (cx if k % 2 == 0 else cy) for k in range(6)]
            points = [(values[0], values[1]), (values[2], values[3]), (values[4], values[5])]
            out.append("<a:cubicBezTo>" + "".join(f"<a:pt {_pt(x, y, viewbox)}/>" for x, y in points)
                       + "</a:cubicBezTo>")
            cx, cy = points[2]
        elif cmd in "Aa":
            rx, ry, rotation = number(), number(), number()
            large, sweep = flag(), flag()
            x, y = number(), number()
            if cmd == "a":
                x, y = cx + x, cy + y
            out.append(_arc(cx, cy, rx, ry, rotation, large, sweep, x, y, viewbox))
            cx, cy = x, y
        else:
            index += 1
    return "".join(out)


def _arc(x1: float, y1: float, rx: float, ry: float, rotation: float,
         large: bool, sweep: bool, x2: float, y2: float, viewbox: float) -> str:
    """SVG 端点参数圆弧 → 若干段三次贝塞尔。"""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return f'<a:lnTo><a:pt {_pt(x2, y2, viewbox)}/></a:lnTo>'
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation % 360)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # 半径不够大时按规范放大
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    sign = -1 if large == sweep else 1
    numerator = max(0.0, rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2)
    denominator = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = sign * math.sqrt(numerator / denominator) if denominator else 0.0
    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        length = math.hypot(ux, uy) * math.hypot(vx, vy)
        value = max(-1.0, min(1.0, dot / length)) if length else 1.0
        result = math.acos(value)
        return -result if (ux * vy - uy * vx) < 0 else result

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    segments = max(1, int(math.ceil(abs(delta) / (math.pi / 2))))
    step = delta / segments
    alpha = 4 / 3 * math.tan(step / 4)
    body = ""
    for index in range(segments):
        a0 = theta1 + index * step
        a1 = a0 + step
        cos0, sin0 = math.cos(a0), math.sin(a0)
        cos1, sin1 = math.cos(a1), math.sin(a1)
        p0 = (cos_phi * rx * cos0 - sin_phi * ry * sin0 + cx,
              sin_phi * rx * cos0 + cos_phi * ry * sin0 + cy)
        p3 = (cos_phi * rx * cos1 - sin_phi * ry * sin1 + cx,
              sin_phi * rx * cos1 + cos_phi * ry * sin1 + cy)
        d0 = (-cos_phi * rx * sin0 - sin_phi * ry * cos0,
              -sin_phi * rx * sin0 + cos_phi * ry * cos0)
        d1 = (-cos_phi * rx * sin1 - sin_phi * ry * cos1,
              -sin_phi * rx * sin1 + cos_phi * ry * cos1)
        c1 = (p0[0] + alpha * d0[0], p0[1] + alpha * d0[1])
        c2 = (p3[0] - alpha * d1[0], p3[1] - alpha * d1[1])
        body += (
            f'<a:cubicBezTo><a:pt {_pt(c1[0], c1[1], viewbox)}/>'
            f'<a:pt {_pt(c2[0], c2[1], viewbox)}/><a:pt {_pt(p3[0], p3[1], viewbox)}/></a:cubicBezTo>'
        )
    return body


__all__ = ["path_commands", "GRID"]
