"""逐页体检 + 确定性修复策略。

浏览器已经给出「裁切/溢出」这类硬事实，但它看不到设计问题：两块文字压在一起、
浅底浅字、字号小到投影看不清、整页空得发慌。这里在**实测几何**上补这些检查，
全部是纯函数——同一份 layout.json 永远得到同一份报告，可单测、可复现。

修复不是「重问模型」，而是一条确定性阶梯（见 next_repair）：
先收字阶与间距，再换更稳的版式，最后减一条要点。每一步都只动构图不动文案语义，
所以修完仍然是用户确认过的那份内容。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.services.ppt_studio.htmlkit import color

# 色标：颜色 + 可选百分比位置。Chromium 会省略 180deg，所以角度单独匹配。
_STOP = re.compile(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))(?:\s+([\d.]+)%)?")
_ANGLE = re.compile(r"([\d.]+)deg")

# 投影可读下限：1280px 画布 = 13.333in，1px = 0.75pt。
# 14px ≈ 10.5pt 是「最后一排还能看清」的底线，16px ≈ 12pt 是舒适线。
TYPE_HARD_FLOOR = 14.0
TYPE_SOFT_FLOOR = 16.0
# 覆盖率：文字与图形墨迹占版心的比例。太低=空，太高=挤。
FILL_SPARSE = 0.085
FILL_CROWDED = 0.60
# 判定两块文字重叠：交集要同时吃掉「矮的那块」四成高度与四分之一面积
OVERLAP_HEIGHT_RATIO = 0.40
OVERLAP_AREA_RATIO = 0.25

# 母题装饰允许出血，不参与越界与覆盖率统计
_MOTIF_PREFIX = "m-"
# 计入「墨迹」的图形类名：卡片容器不算，它只是底，不是信息
_INK_SHAPES = {
    "bar-fill", "col-bar", "lg-fill", "lg-dot", "tl-dot", "tl-line",
    "rule", "rule--accent", "m-bar", "chart-delta", "chip",
}
# 覆盖率天生就低的版式：封面/章节/结尾靠留白造气势，表格与附录靠行距造可读性
_SPARSE_EXEMPT = {"cover", "section", "end", "quote", "table", "appendix"}
# 版式降级表：素材要求高的 → 要求低的。None 表示这一版式已经最稳，只能减内容。
_FALLBACK = {
    "chart": "cards",
    "cards": "bullets",
    "stat": "cards",
    "metric": "bullets",
    "two_col": "bullets",
    "table": "bullets",
    "timeline": "bullets",
    "quote": "bullets",
}


@dataclass
class Issue:
    """一条体检结论。error 会触发修复，warn 只记进 fidelity 供人工判断。"""

    rule: str
    severity: str  # error | warn
    detail: str
    node_id: str = ""

    def payload(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "detail": self.detail, "node_id": self.node_id}


@dataclass
class PageReport:
    index: int
    layout: str
    issues: list[Issue] = field(default_factory=list)
    fill: float = 0.0
    min_contrast: float = 21.0
    min_type: float = 99.0
    attempts: int = 0
    repairs: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def payload(self) -> dict:
        return {
            "index": self.index,
            "layout": self.layout,
            "ok": self.ok,
            "fill": round(self.fill, 4),
            "min_contrast": round(self.min_contrast, 2),
            "min_type": round(self.min_type, 1),
            "attempts": self.attempts,
            "repairs": list(self.repairs),
            "issues": [i.payload() for i in self.issues],
        }


# ---------------------------------------------------------------- 几何工具


def _contains(rect: dict, x: float, y: float) -> bool:
    return rect["x"] <= x <= rect["x"] + rect["w"] and rect["y"] <= y <= rect["y"] + rect["h"]


def _intersection(a: dict, b: dict) -> tuple[float, float]:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, w), max(0.0, h)


def _fill_of(style: dict, rect: dict | None = None, x: float = 0.0, y: float = 0.0) -> tuple[str, float]:
    """盒子在 (x, y) 处的有效填充色与不透明度。透明返回 alpha=0。

    渐变必须按位置取色：封面 kicker 在左上角（渐变最暗端），拿中点色去判
    对比度会误报——实测差了近 2 个对比度单位。
    """
    opacity = float(style.get("opacity", 1) or 0)
    gradient = style.get("bgImage") or "none"
    if gradient.startswith("linear-gradient"):
        stops = color_stops(gradient)
        if stops:
            if rect and rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
                return _gradient_at(gradient, stops, rect, x, y, opacity)
            mid = stops[len(stops) // 2]
            return mid[0], mid[1] * opacity
    hexed, alpha = color.parse(style.get("bg") or "")
    return hexed, alpha * opacity


def color_stops(css: str) -> list[tuple[str, float, float]]:
    """渐变色标 → [(HEX, alpha, 位置 0~1)]。没写位置的按均分补。"""
    out: list[tuple[str, float, float]] = []
    for match in _STOP.finditer(css):
        hexed, alpha = color.parse(match.group(1))
        if alpha <= 0:
            continue
        pos = float(match.group(2)) / 100 if match.group(2) else None
        out.append((hexed, alpha, pos))
    if not out:
        return []
    if out[0][2] is None:
        out[0] = (out[0][0], out[0][1], 0.0)
    if out[-1][2] is None:
        out[-1] = (out[-1][0], out[-1][1], 1.0)
    # 中间缺位置的色标：在前后两个已知位置之间线性补
    known = [i for i, s in enumerate(out) if s[2] is not None]
    for a, b in zip(known, known[1:]):
        gap = b - a
        for step in range(1, gap):
            t = step / gap
            pos = out[a][2] + (out[b][2] - out[a][2]) * t
            out[a + step] = (out[a + step][0], out[a + step][1], pos)
    return out


def _gradient_at(css: str, stops: list[tuple[str, float, float]], rect: dict,
                 x: float, y: float, opacity: float) -> tuple[str, float]:
    """CSS 渐变在画布某点的取色。角度口径：0deg 向上，顺时针递增。"""
    match = _ANGLE.search(css)
    angle = math.radians(float(match.group(1)) if match else 180.0)
    dx, dy = math.sin(angle), -math.cos(angle)
    w, h = rect["w"], rect["h"]
    length = abs(w * dx) + abs(h * dy)
    if length <= 0:
        return stops[0][0], stops[0][1] * opacity
    cx, cy = rect["x"] + w / 2, rect["y"] + h / 2
    t = 0.5 + ((x - cx) * dx + (y - cy) * dy) / length
    t = max(0.0, min(1.0, t))
    before = stops[0]
    for stop in stops:
        if stop[2] <= t:
            before = stop
        else:
            span = stop[2] - before[2]
            ratio = (t - before[2]) / span if span > 1e-6 else 0.0
            hexed = color.blend(stop[0], before[0], 1 - ratio)
            alpha = before[1] + (stop[1] - before[1]) * ratio
            return hexed, alpha * opacity
    return before[0], before[1] * opacity


def _backdrop(nodes: list[dict], upto: int, x: float, y: float, fallback: str) -> str:
    """按绘制顺序合成该点下方的所有填充——画家算法，和浏览器看到的一致。"""
    base = fallback
    for node in nodes[:upto]:
        if node.get("kind") == "text":
            continue
        rect = node.get("rect") or {}
        if not rect or not _contains(rect, x, y):
            continue
        hexed, alpha = _fill_of(node.get("style") or {}, rect, x, y)
        if alpha <= 0.01:
            continue
        base = color.blend(hexed, base, 1 - alpha)
    return base


# ---------------------------------------------------------------- 单项检查


def _check_bounds(nodes: list[dict], tokens: dict, issues: list[Issue]) -> None:
    for node in nodes:
        cls = str(node.get("className") or "")
        if node.get("id") == "root" or cls.startswith(_MOTIF_PREFIX):
            continue
        rect = node.get("rect") or {}
        if not rect:
            continue
        if (rect["x"] < -1 or rect["y"] < -1
                or rect["x"] + rect["w"] > tokens["canvas_w"] + 1
                or rect["y"] + rect["h"] > tokens["canvas_h"] + 1):
            issues.append(Issue(
                "out-of-canvas", "error",
                f"{cls or node.get('tag')} 越出画布（{round(rect['x'])},{round(rect['y'])} "
                f"{round(rect['w'])}×{round(rect['h'])}）",
                str(node.get("id") or ""),
            ))


def _check_overlap(nodes: list[dict], issues: list[Issue]) -> None:
    """跨文本块的行矩形相撞。同块内的行天然不重叠，不用比。"""
    lines: list[tuple[str, str, dict]] = []
    for node in nodes:
        if node.get("kind") != "text":
            continue
        owner = str(node.get("id") or "")
        cls = str(node.get("className") or "")
        for line in node.get("lines") or []:
            if line.get("w", 0) > 1 and line.get("h", 0) > 1:
                lines.append((owner, cls, line))
    seen: set[tuple[str, str]] = set()
    for i in range(len(lines)):
        owner_a, cls_a, a = lines[i]
        for j in range(i + 1, len(lines)):
            owner_b, cls_b, b = lines[j]
            if owner_a == owner_b:
                continue
            w, h = _intersection(a, b)
            if h <= 1 or w <= 1:
                continue
            short = min(a["h"], b["h"])
            area = min(a["w"] * a["h"], b["w"] * b["h"])
            if h < short * OVERLAP_HEIGHT_RATIO or w * h < area * OVERLAP_AREA_RATIO:
                continue
            key = tuple(sorted((owner_a, owner_b)))
            if key in seen:
                continue
            seen.add(key)
            issues.append(Issue(
                "text-overlap", "error",
                f"{cls_a or owner_a} 与 {cls_b or owner_b} 文字重叠 {round(w)}×{round(h)}px",
                owner_a,
            ))


def _check_type(nodes: list[dict], issues: list[Issue]) -> float:
    smallest = 99.0
    worst_id = ""
    for node in nodes:
        if node.get("kind") != "text":
            continue
        for line in node.get("lines") or []:
            for run in line.get("runs") or []:
                if not str(run.get("text") or "").strip():
                    continue
                size = float(run.get("fontSize") or 0)
                if 0 < size < smallest:
                    smallest, worst_id = size, str(node.get("id") or "")
    if smallest < TYPE_HARD_FLOOR:
        issues.append(Issue("type-too-small", "error",
                            f"最小字号 {smallest:.0f}px 低于投影下限 {TYPE_HARD_FLOOR:.0f}px", worst_id))
    elif smallest < TYPE_SOFT_FLOOR:
        issues.append(Issue("type-small", "warn",
                            f"最小字号 {smallest:.0f}px 偏小（建议 ≥{TYPE_SOFT_FLOOR:.0f}px）", worst_id))
    return smallest


def _check_contrast(nodes: list[dict], tokens: dict, issues: list[Issue]) -> float:
    fallback = "#" + tokens["colors"]["bg"]
    lowest = 21.0
    for position, node in enumerate(nodes):
        if node.get("kind") != "text":
            continue
        for line in node.get("lines") or []:
            runs = [r for r in (line.get("runs") or []) if str(r.get("text") or "").strip()]
            if not runs:
                continue
            cx = line["x"] + line["w"] / 2
            cy = line["y"] + line["h"] / 2
            bg = _backdrop(nodes, position, cx, cy, fallback)
            for run in runs:
                size = float(run.get("fontSize") or 23)
                try:
                    bold = float(run.get("fontWeight") or 400) >= 600
                except (TypeError, ValueError):
                    bold = str(run.get("fontWeight")) in ("bold", "bolder")
                # WCAG 大字号口径：≥24px 常规 或 ≥18.7px 粗体
                large = size >= 24 or (bold and size >= 18.7)
                need = 3.0 if large else 4.5
                ratio = color.contrast(str(run.get("color") or ""), bg)
                lowest = min(lowest, ratio)
                # 0.05 容差：4.4996 显示成「4.5 < 4.5」只会让人困惑
                if ratio >= need - 0.05:
                    continue
                severity = "error" if ratio < need * 0.72 else "warn"
                issues.append(Issue(
                    "low-contrast", severity,
                    f"{str(node.get('className') or '')} 「{str(run.get('text'))[:14]}」"
                    f"对比度 {ratio:.1f}:1 < {need:.1f}:1",
                    str(node.get("id") or ""),
                ))
    return lowest


def _fill_ratio(nodes: list[dict], tokens: dict) -> float:
    """墨迹覆盖率：文字行 + 数据图形占版心的比例（4px 栅格求并集，够准且便宜）。"""
    margin = float(tokens.get("margin") or 64)
    left, top = margin, margin
    right = tokens["canvas_w"] - margin
    bottom = tokens["canvas_h"] - margin * 0.55
    cell = 4
    cols = int((right - left) // cell) + 1
    rows = int((bottom - top) // cell) + 1
    if cols <= 0 or rows <= 0:
        return 0.0
    grid = bytearray(cols * rows)
    rects: list[dict] = []
    for node in nodes:
        cls = str(node.get("className") or "")
        if cls.startswith(_MOTIF_PREFIX) or node.get("id") == "root":
            continue
        kind = node.get("kind")
        if kind == "text":
            rects.extend(line for line in (node.get("lines") or []) if line.get("w", 0) > 1)
        elif kind == "directive":
            rects.append(node["rect"])
        elif kind == "shape" and any(part in _INK_SHAPES for part in cls.split()):
            rects.append(node["rect"])
    for rect in rects:
        x0 = max(0, int((rect["x"] - left) // cell))
        x1 = min(cols - 1, int((rect["x"] + rect["w"] - left) // cell))
        y0 = max(0, int((rect["y"] - top) // cell))
        y1 = min(rows - 1, int((rect["y"] + rect["h"] - top) // cell))
        for y in range(y0, y1 + 1):
            row = y * cols
            for x in range(x0, x1 + 1):
                grid[row + x] = 1
    return sum(grid) / float(cols * rows)


# ---------------------------------------------------------------- 对外入口


def inspect(measured: dict, tokens: dict, *, layout: str = "") -> PageReport:
    """一页实测结果 → 体检报告。纯函数，不改动输入。"""
    nodes = measured.get("nodes") or []
    report = PageReport(index=int(measured.get("index") or 0), layout=layout)
    issues = report.issues
    for raw in measured.get("diagnostics") or []:
        rule = str(raw.get("ruleId") or "unknown")
        severity = "error" if rule in ("text-clipped", "canvas-overflow", "svg-without-directive") else "warn"
        issues.append(Issue(rule, severity, str(raw.get("detail") or ""), str(raw.get("className") or "")))
    _check_bounds(nodes, tokens, issues)
    _check_overlap(nodes, issues)
    report.min_type = _check_type(nodes, issues)
    report.min_contrast = _check_contrast(nodes, tokens, issues)
    report.fill = _fill_ratio(nodes, tokens)
    if report.fill < FILL_SPARSE and layout not in _SPARSE_EXEMPT:
        issues.append(Issue("sparse", "warn", f"版心墨迹仅 {report.fill * 100:.0f}%，页面偏空"))
    elif report.fill > FILL_CROWDED:
        issues.append(Issue("crowded", "warn", f"版心墨迹 {report.fill * 100:.0f}%，页面偏挤"))
    # 同一条规则在一页里重复几十次没有意义，按 (rule, node) 去重后限量
    unique: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.rule, issue.node_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    report.issues = unique[:24]
    return report


def summarize(reports: list[PageReport]) -> dict:
    """整份体检摘要，进 fidelity 字段供前端与日志使用。"""
    errors = sum(len(r.errors) for r in reports)
    warns = sum(len(r.issues) - len(r.errors) for r in reports)
    rules: dict[str, int] = {}
    for report in reports:
        for issue in report.issues:
            rules[issue.rule] = rules.get(issue.rule, 0) + 1
    fills = [r.fill for r in reports if r.layout not in ("cover", "section", "end")]
    return {
        "pages": len(reports),
        "clean_pages": sum(1 for r in reports if r.ok),
        "errors": errors,
        "warnings": warns,
        "rules": rules,
        "repairs": sum(len(r.repairs) for r in reports),
        "avg_fill": round(sum(fills) / len(fills), 4) if fills else 0.0,
        "min_contrast": round(min((r.min_contrast for r in reports), default=21.0), 2),
    }


# ---------------------------------------------------------------- 修复阶梯


@dataclass
class Repair:
    """一次构图修复。scale 只作用于本页，drop 只减要点条数，不改文案。"""

    action: str  # tighten | relayout | trim
    layout: str = ""
    scale: float = 1.0
    drop: int = 0

    def label(self) -> str:
        if self.action == "tighten":
            return f"收字阶 ×{self.scale:.2f}"
        if self.action == "relayout":
            return f"改版式 → {self.layout}"
        return f"减 {self.drop} 条要点"


def next_repair(report: PageReport, *, has_cards: bool = False) -> Repair | None:
    """按已尝试次数给出下一步；返回 None 表示阶梯走完，接受当前结果。

    阶梯顺序是「代价从小到大」：收字阶几乎看不出来 → 换版式改变构图 →
    减要点才真正动内容密度。所以先试便宜的。
    """
    step = len(report.repairs)
    layout = report.layout
    if step == 0:
        return Repair("tighten", scale=0.93)
    if step == 1:
        target = _FALLBACK.get(layout)
        if target == "cards" and not has_cards:
            target = "bullets"
        if target and target != layout:
            return Repair("relayout", layout=target, scale=0.97)
        return Repair("trim", drop=1, scale=0.95)
    if step == 2:
        return Repair("trim", drop=1, scale=0.93)
    return None


__all__ = [
    "Issue", "PageReport", "Repair", "inspect", "summarize", "next_repair",
    "TYPE_HARD_FLOOR", "TYPE_SOFT_FLOOR", "FILL_SPARSE", "FILL_CROWDED",
]