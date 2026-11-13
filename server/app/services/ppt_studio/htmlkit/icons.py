"""内置线性图标（Lucide 1.69.0，ISC，经 lucide-react 抽取）。

这里只存**几何**，不存字体、不存位图。生成 HTML 时它是一段带 `data-pptx="icon"`
指令的节点（截图里看得见），写成 pptx 时由 `icon_geom` 转成自定义几何形状
（每个图标都是可编辑的矢量，不依赖观看端装字体）。

用图标的分寸：只做「指标名前面的一个小字形」，不做「图标块 + 标题」那种
模板套路（检测器的 `icon-tile-stack` 就是冲它去的）。
"""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path

_DATA = json.loads((Path(__file__).with_name("icons.json")).read_text(encoding="utf-8"))
DEFAULT_SIZE = 24.0

# 标签关键词 → 图标。刻意少而稳：命中不了就不放图标，不硬凑。
KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("联网", "网络", "在线", "设备"), "wifi"),
    (("换线", "耗时", "时长", "分钟", "小时", "响应"), "timer"),
    (("良率", "合格", "质量", "一次通过", "不良"), "badge-check"),
    (("成本", "费用", "单价", "金额", "损益"), "circle-dollar-sign"),
    (("交付", "准时", "物流", "订单", "发货"), "truck"),
    (("增长", "提升", "上升", "爬升", "提高"), "trending-up"),
    (("下降", "降低", "减少", "回落", "压缩"), "trending-down"),
    (("效率", "产能", "节拍", "利用率"), "gauge"),
    (("人员", "团队", "员工", "编制"), "users"),
    (("合规", "安全", "风险", "认证", "审计"), "shield-check"),
    (("目标", "达成", "指标", "承诺"), "target"),
    (("能耗", "用电", "能源", "碳"), "zap"),
    (("数据", "系统", "平台", "数据库", "采集"), "database"),
    (("架构", "分层", "模块", "底座"), "layers"),
    (("产量", "出货", "包裹", "库存"), "package"),
    (("工厂", "产线", "车间", "制造"), "factory"),
    (("计划", "日程", "周期", "年度", "季度"), "calendar-days"),
)


def name_for(label: str) -> str | None:
    """按标签里的关键词挑一个图标；挑不到返回 None（宁缺毋滥）。"""
    text = label or ""
    for words, name in KEYWORDS:
        if any(word in text for word in words):
            return name if name in _DATA else None
    return None


def nodes(name: str) -> list | None:
    icon = _DATA.get(name)
    return icon["node"] if icon else None


def viewbox(name: str) -> float:
    icon = _DATA.get(name)
    return float(icon["size"]) if icon else DEFAULT_SIZE


def svg(name: str, *, size: float, color: str, stroke: float = 2.0,
        class_name: str = "metric-icon") -> str:
    """带指令的图标节点：截图里是 SVG，pptx 里是矢量形状。"""
    icon_nodes = nodes(name)
    if not icon_nodes:
        return ""
    payload = json.dumps(icon_nodes, ensure_ascii=False, separators=(",", ":"))
    return (
        f'<div class="{class_name}" data-pptx="icon" '
        f'data-nodes="{html_lib.escape(payload, quote=True)}" '
        f'data-vb="{viewbox(name):g}" data-color="{color}" data-stroke="{stroke:g}">'
        f'{_svg(icon_nodes, viewbox(name), color, stroke)}</div>'
    )


def _svg(icon_nodes: list, box: float, color: str, stroke: float) -> str:
    """只给截图看的孪生 SVG；pptx 侧由指令画。"""
    parts: list[str] = []
    for tag, attrs in icon_nodes:
        if tag == "path":
            parts.append(f'<path d="{html_lib.escape(attrs.get("d") or "", quote=True)}"/>')
        elif tag in ("circle", "ellipse", "rect", "line"):
            rendered = " ".join(
                f'{key}="{html_lib.escape(str(value), quote=True)}"'
                for key, value in attrs.items() if key != "key"
            )
            parts.append(f"<{tag} {rendered}/>")
        elif tag in ("polyline", "polygon"):
            points = html_lib.escape(attrs.get("points") or "", quote=True)
            parts.append(f'<{tag} points="{points}"/>')
    return (
        f'<svg viewBox="0 0 {box:g} {box:g}" width="100%" height="100%" fill="none" '
        f'stroke="{color}" stroke-width="{stroke:g}" stroke-linecap="round" '
        f'stroke-linejoin="round">{"".join(parts)}</svg>'
    )


__all__ = ["name_for", "nodes", "svg", "viewbox", "KEYWORDS", "DEFAULT_SIZE"]