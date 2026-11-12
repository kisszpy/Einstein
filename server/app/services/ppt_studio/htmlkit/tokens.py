"""设计令牌：一个「家族」= 配色 + 字体 + 母题 + 图表色带 + 可调旋钮。

与 designs.py 的关系：designs 是配色的唯一来源（UI 风格卡的色块也读它），
这里在其上补 HTML 渲染需要的东西——封面渐变、玻璃卡片、图表 6 色带、
语义色、字体栈、母题类型，以及用户在「设计台」上能实时拧的旋钮。

旋钮全部有上限 clamp：前端滑到极端也不会把版式拧烂（字号不会小于 16px、
留白不会吃掉版心、圆角不会超过卡片短边的一半）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.ppt_studio import designs
from app.services.ppt_studio.htmlkit import color

# 画布与 pptx 页面严格 96dpi 对齐：13.333in × 96 = 1280px，7.5in × 96 = 720px。
# 所有几何换算都建立在这个等式上（px → EMU 乘 9525，px → pt 乘 0.75）。
CANVAS_W, CANVAS_H = 1280, 720
PX_PER_INCH = 96.0
EMU_PER_PX = 9525.0
PT_PER_PX = 0.75


def canvas_for(width_in: float, height_in: float) -> tuple[int, int]:
    """模板英寸 → 画布像素，归一到 720 高。

    96dpi 下 16:9 恰好 1280、4:3 恰好 960，两者都能与 pptx 页面精确对齐，
    EMU_PER_PX / PT_PER_PX 因此仍是常量。极端尺寸（A4 纵向、超宽横幅）
    归一后物理尺寸会变，调用方要把这件事写进降级说明。
    """
    if width_in <= 0 or height_in <= 0:
        return CANVAS_W, CANVAS_H
    return max(480, min(2048, int(round(width_in / height_in * CANVAS_H)))), CANVAS_H

# 本机确实装了的字体：Chromium 与 PowerPoint 用同一份字库，实测行宽才能对齐。
# 中文正文用雅黑（PPT 端最稳），拉丁数字用 Inter（几何感强、字重齐全），
# 衬线家族用 Noto Serif SC，等宽标签用 Consolas。
FONT_SANS = "Microsoft YaHei"
FONT_LATIN = "Inter"
FONT_SERIF = "Noto Serif SC"
FONT_MONO = "Consolas"

# 语义色：图表里的「达标/预警/风险」与状态 chip 共用，跨家族保持一致语义
SEMANTIC = {
    "success": "1F9D6B",
    "warning": "D98A15",
    "danger": "D6453C",
    "info": "2F6BDC",
}


@dataclass(frozen=True)
class Family:
    """一个设计家族的美术方向（配色之外的部分）。"""

    key: str
    mode: str  # dark | light —— 内容页底色基调
    motif: str  # rule 贯通细线 | index 章节编号 | glass 玻璃卡 | grid 细线网格
    display_font: str
    body_font: str
    cover_stops: tuple[str, ...]  # 封面渐变（≥2 个真实不同色相）
    chart: tuple[str, ...]  # 6~8 色带，色相 + 明度双重可分
    grain: float = 0.0  # 封面颗粒感强度（0 = 关闭）
    tagline: str = ""


_FAMILIES: dict[str, Family] = {
    "midnight": Family(
        key="midnight",
        mode="dark",
        motif="rule",
        display_font=FONT_LATIN,
        body_font=FONT_SANS,
        cover_stops=("050A18", "0B1223", "1B2E63", "24408F"),
        chart=("4F6BF6", "14B8C4", "F2A93B", "E2557B", "7C5CFF", "3FBF7F", "9AA7C7"),
        grain=0.05,
        tagline="深蓝夜色 · 贯通细线 · 商务稳重",
    ),
    "graphite": Family(
        key="graphite",
        mode="dark",
        motif="index",
        display_font=FONT_LATIN,
        body_font=FONT_SANS,
        cover_stops=("0C0C0E", "1A1A1D", "2E2A22", "4A3F26"),
        chart=("C2A15A", "E0E0E4", "8A8A93", "D97742", "6E8FA8", "A8B56A", "B07C9E"),
        grain=0.06,
        tagline="石墨黑白 · 金色点缀 · 极简克制",
    ),
    "ocean": Family(
        key="ocean",
        mode="dark",
        motif="glass",
        display_font=FONT_LATIN,
        body_font=FONT_SANS,
        cover_stops=("021624", "041F33", "0A4A6B", "0E7490"),
        chart=("0E9BD8", "10B981", "22D3EE", "F5B942", "5B7CFA", "E2607A", "8FB8CC"),
        grain=0.04,
        tagline="深海蓝绿 · 玻璃卡片 · 科技质感",
    ),
    "sunset": Family(
        key="sunset",
        mode="dark",
        motif="glass",
        display_font=FONT_LATIN,
        body_font=FONT_SANS,
        cover_stops=("1C1030", "3A1B4E", "7A1E52", "C23061"),
        chart=("E11D63", "E8A03C", "8B5CF6", "22B8CF", "F2748C", "6D8BF5", "C9A0DC"),
        grain=0.05,
        tagline="紫红暮色 · 大渐变 · 发布会气质",
    ),
    "forest": Family(
        key="forest",
        mode="dark",
        motif="rule",
        display_font=FONT_LATIN,
        body_font=FONT_SANS,
        cover_stops=("07160F", "0E241C", "17402C", "24603A"),
        chart=("1F9D6B", "D9A441", "3FA9C9", "C4614A", "7FBF5C", "8E7BD6", "9FC3AE"),
        grain=0.05,
        tagline="墨绿松林 · 稳健汇报 · 自然沉静",
    ),
    "ivory": Family(
        key="ivory",
        mode="light",
        motif="grid",
        display_font=FONT_SERIF,
        body_font=FONT_SANS,
        cover_stops=("FFFDF8", "FAF7F0", "F0E4CE", "E7D3B6"),
        chart=("B4552A", "2F6B5B", "3A6EA5", "C08A2E", "8C4A6B", "5E7A46", "9A8878"),
        grain=0.08,
        tagline="象牙纸张 · 衬线标题 · 学术研究",
    ),
}


@dataclass
class Knobs:
    """设计台旋钮。前端每次改动都实时重预览，所以这里必须便宜且无副作用。"""

    density: str = "normal"  # loose | normal | dense
    type_scale: float = 1.0  # 0.88 ~ 1.14 字阶总倍率
    radius: int = 14  # 0 ~ 28 卡片圆角
    whitespace: float = 1.0  # 0.8 ~ 1.25 版心外边距倍率
    accent: str = ""  # 强调色覆盖（空 = 用家族色）
    graphic: str = "auto"  # auto | flat | gradient | outline
    motif: bool = True  # 母题装饰（封面大圆/网格/贯穿线）
    footer: bool = True  # 页脚页码与来源
    seed: int = 0  # 「随机一版」的种子：只影响构图变体，不影响内容

    @classmethod
    def from_style(cls, style: Any) -> "Knobs":
        get = style.get if isinstance(style, dict) else lambda k, d=None: getattr(style, k, d)
        knobs = cls()
        density = str(get("density", "normal") or "normal")
        knobs.density = density if density in ("loose", "normal", "dense") else "normal"
        knobs.type_scale = _clamp_float(get("type_scale", 1.0), 0.88, 1.14, 1.0)
        knobs.radius = int(_clamp_float(get("radius", 14), 0, 28, 14))
        knobs.whitespace = _clamp_float(get("whitespace", 1.0), 0.8, 1.25, 1.0)
        accent = str(get("accent", "") or "").strip()
        knobs.accent = color.parse(accent)[0] if accent and color.parse(accent)[1] > 0 else ""
        graphic = str(get("graphic", "auto") or "auto")
        knobs.graphic = graphic if graphic in ("auto", "flat", "gradient", "outline") else "auto"
        knobs.motif = _as_bool(get("motif", True), True)
        knobs.footer = _as_bool(get("footer", True), True)
        knobs.seed = int(_clamp_float(get("seed", 0), 0, 9999, 0))
        return knobs

    def payload(self) -> dict:
        return {
            "density": self.density,
            "type_scale": round(self.type_scale, 3),
            "radius": self.radius,
            "whitespace": round(self.whitespace, 3),
            "accent": self.accent,
            "graphic": self.graphic,
            "motif": self.motif,
            "footer": self.footer,
            "seed": self.seed,
        }


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no", "")
    if value is None:
        return default
    return bool(value)


# 密度 → (版心外边距 px, 块间距 px, 卡片内边距 px)
_DENSITY = {
    "loose": (76, 34, 30),
    "normal": (64, 26, 24),
    "dense": (54, 20, 19),
}


def family_of(preset_key: str) -> Family:
    return _FAMILIES.get((preset_key or "").strip().lower(), _FAMILIES[designs.DEFAULT_KEY])


def family_from_payload(payload: dict) -> Family:
    """上传模板抽出的美术方向 → Family。缺项按午夜蓝补，绝不抛。"""
    base = _FAMILIES[designs.DEFAULT_KEY]
    chart = tuple(str(h).lstrip("#").upper() for h in (payload.get("chart") or []) if h)
    stops = tuple(str(h).lstrip("#").upper() for h in (payload.get("cover_stops") or []) if h)
    mode = str(payload.get("mode") or base.mode)
    return Family(
        key=str(payload.get("key") or "template"),
        mode=mode if mode in ("dark", "light") else base.mode,
        motif=str(payload.get("motif") or base.motif),
        display_font=str(payload.get("display_font") or base.display_font),
        body_font=str(payload.get("body_font") or base.body_font),
        cover_stops=stops or base.cover_stops,
        chart=chart or base.chart,
        grain=float(payload.get("grain") or 0.0),
        tagline=str(payload.get("tagline") or ""),
    )


def all_families() -> list[dict]:
    """给前端「设计家族」选择器：色块 + 一句美术方向，不暴露内部实现。"""
    out: list[dict] = []
    for preset in designs.all_presets():
        fam = family_of(preset["key"])
        palette = preset["palette"]
        out.append(
            {
                "id": designs.preset_id(fam.key),
                "key": fam.key,
                "kind": "builtin",
                "name": preset["name"],
                "tagline": fam.tagline or preset["tagline"],
                "mode": fam.mode,
                "motif": fam.motif,
                "swatches": [palette["bg"], palette["accent"], palette["accent2"], palette["paper"]],
                "chart": list(fam.chart),
                "ratio": "16:9",
                "notes": [],
            }
        )
    return out


def resolve_tokens(preset: dict, knobs: Knobs | None = None) -> dict:
    """preset + 旋钮 → 一份完整令牌（CSS 变量字典 + 元信息）。

    深色家族的内容页也走深底：投影环境下深底更稳，且与封面同一世界观；
    ivory 是唯一浅色家族（纸张感，适合打印）。
    """
    knobs = knobs or Knobs()
    fam = family_from_payload(preset["family"]) if preset.get("family") else family_of(preset.get("key", ""))
    palette = dict(preset.get("palette") or designs.default_preset()["palette"])
    dark = fam.mode == "dark"
    canvas_w, canvas_h = preset.get("canvas") or (CANVAS_W, CANVAS_H)

    bg = palette["bg"] if dark else palette["paper"]
    surface = palette["bg_soft"] if dark else palette["surface"]
    ink = palette["on_dark"] if dark else palette["ink"]
    muted = palette["on_dark_muted"] if dark else palette["ink_muted"]
    accent = knobs.accent or palette["accent"]
    accent2 = palette["accent2"]
    accent3 = fam.chart[3]

    # 描边与柔和色由底色派生，保证任何家族下都「刚好看得见」
    line = color.blend(ink, bg, 0.86 if dark else 0.80)
    hairline = color.blend(ink, bg, 0.92 if dark else 0.88)
    card = color.blend(surface, bg, 0.25) if dark else palette["paper"]
    card_alt = color.blend(surface, bg, 0.55) if dark else palette["surface"]
    accent_soft = color.blend(accent, bg, 0.80 if dark else 0.86)
    # 强调色作为文字时必须达标：深底提亮、浅底压深。
    # 两个底都要过——页面上它既直接落在 bg 上（kicker），也落在 accent_soft 上（chip）。
    towards_accent = "FFFFFF" if dark else "000000"
    accent_ink = color.readable_on(bg, (accent, color.blend(accent, towards_accent, 0.28),
                                        color.blend(accent, towards_accent, 0.5)), 4.5)
    accent_ink = color.readable_on(
        accent_soft,
        (accent_ink, color.blend(accent_ink, towards_accent, 0.16),
         color.blend(accent_ink, towards_accent, 0.34)),
        4.5,
    )
    on_accent = color.readable_on(accent, ("FFFFFF", "0B1020"), 4.5)
    # 页脚/来源这类「次要但仍要读」的文字：先按明度调暗，再兜底到达标为止。
    # 直接 blend 到 0.42 在深底上只有 3.6:1，投影时页码几乎看不见。
    faint = color.readable_on(
        bg,
        (color.blend(muted, bg, 0.42), color.blend(muted, bg, 0.26), muted),
        4.5,
    )

    margin, gap, pad = _DENSITY[knobs.density]
    # 上传模板量出来的版心是基准，密度与留白旋钮仍按倍率作用其上
    base_margin = float(preset.get("margin_px") or _DENSITY["normal"][0])
    margin = int(round(base_margin * (margin / _DENSITY["normal"][0]) * knobs.whitespace))
    margin = max(44, min(96, margin))
    scale = knobs.type_scale

    def px(base: float, floor: float = 12.0) -> int:
        return int(round(max(floor, base * scale)))

    # 字阶按「投影可读」定：1280px 画布 = 13.333in，1pt = 1.333px。
    # body 23px ≈ 17pt、h1 44px ≈ 33pt——低于这个尺度上台就看不清。
    types = {
        "cover": px(84, 48),
        "h1": px(44, 28),
        "h2": px(30, 21),
        "h3": px(24, 18),
        "lead": px(25, 18),
        "body": px(23, 17),
        "small": px(19, 15),
        "kicker": px(16, 13),
        "stat": px(78, 44),
        "stat_sm": px(52, 32),
        "index": px(132, 64),
        "quote": px(54, 32),
    }

    tokens = {
        "key": fam.key,
        "mode": fam.mode,
        "canvas_w": int(canvas_w),
        "canvas_h": int(canvas_h),
        "motif": fam.motif if knobs.motif else "none",
        "graphic": knobs.graphic,
        "seed": knobs.seed,
        "font_body": fam.body_font,
        "font_display": fam.display_font,
        "font_mono": FONT_MONO,
        "margin": margin,
        "gap": gap,
        "pad": pad,
        "radius": knobs.radius,
        "footer": knobs.footer,
        "types": types,
        "colors": {
            "bg": bg,
            "surface": surface,
            "card": card,
            "card_alt": card_alt,
            "line": line,
            "hairline": hairline,
            "ink": ink,
            "muted": muted,
            "faint": faint,
            "accent": accent,
            "accent_ink": accent_ink,
            "accent_soft": accent_soft,
            "accent2": accent2,
            "accent3": accent3,
            "on_accent": on_accent,
            **SEMANTIC,
        },
        "cover_stops": list(fam.cover_stops),
        "chart": list(fam.chart),
        "grain": fam.grain,
    }
    tokens["css_vars"] = css_vars(tokens)
    return tokens


def css_vars(tokens: dict) -> dict[str, str]:
    """令牌 → CSS 自定义属性（stylesheet 只认这些变量，不写死任何颜色）。"""
    colors = tokens["colors"]
    types = tokens["types"]
    variables = {f"--c-{name.replace('_', '-')}": f"#{value}" for name, value in colors.items()}
    variables.update({f"--t-{name.replace('_', '-')}": f"{value}px" for name, value in types.items()})
    variables.update(
        {
            "--margin": f"{tokens['margin']}px",
            "--gap": f"{tokens['gap']}px",
            "--pad": f"{tokens['pad']}px",
            "--radius": f"{tokens['radius']}px",
            "--canvas-w": f"{tokens['canvas_w']}px",
            "--canvas-h": f"{tokens['canvas_h']}px",
            "--font-body": f'"{tokens["font_body"]}", "Noto Sans SC", sans-serif',
            "--font-display": f'"{tokens["font_display"]}", "{tokens["font_body"]}", sans-serif',
            "--font-mono": f'"{tokens["font_mono"]}", monospace',
        }
    )
    for index, hue in enumerate(tokens["chart"], start=1):
        variables[f"--chart-{index}"] = f"#{hue}"
    return variables


def cover_gradient(tokens: dict) -> str:
    """封面渐变：真跨色相的多停靠点，不是同色明暗。"""
    stops = tokens["cover_stops"]
    if len(stops) < 2:
        return f"#{tokens['colors']['bg']}"
    parts = [f"#{hexed} {round(i * 100 / (len(stops) - 1))}%" for i, hexed in enumerate(stops)]
    return f"linear-gradient(128deg, {', '.join(parts)})"


def chart_hue(tokens: dict, index: int) -> str:
    ramp = tokens["chart"] or [tokens["colors"]["accent"]]
    return ramp[index % len(ramp)]


@dataclass
class DeckPlan:
    """整份文稿的构图计划：每页版式 + 变体，一次性定好保证全篇节奏。"""

    layouts: list[str] = field(default_factory=list)
    variants: list[int] = field(default_factory=list)
    accents: list[int] = field(default_factory=list)