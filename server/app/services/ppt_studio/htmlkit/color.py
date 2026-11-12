"""颜色工具：混合 / 亮度 / WCAG 对比度 / rgba 解析。

lint 要用对比度判「浅底浅字」，tokens 要用混合派生柔和色，pptx_writer 要把
CSS 颜色串解析成 RGB + 透明度——三处共用一套，避免各写一份口径漂移。
"""

from __future__ import annotations

import colorsys
import re

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB = re.compile(
    r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*([\d.]+%?)\s*)?\)"
)


def parse(value: str) -> tuple[str, float]:
    """CSS 颜色 → (六位大写 HEX, alpha 0~1)。认不出的返回 ("000000", 0.0)。

    alpha=0 表示「完全透明/无法解析」，调用方据此跳过绘制。
    """
    text = (value or "").strip()
    if not text or text in ("transparent", "none"):
        return "000000", 0.0
    m = _HEX.match(text)
    if m:
        body = m.group(1)
        if len(body) == 3:
            body = "".join(ch * 2 for ch in body)
        return body.upper(), 1.0
    m = _RGB.match(text)
    if m:
        r, g, b = (max(0, min(255, int(round(float(m.group(i)))))) for i in (1, 2, 3))
        raw_alpha = m.group(4)
        alpha = 1.0
        if raw_alpha:
            alpha = float(raw_alpha[:-1]) / 100 if raw_alpha.endswith("%") else float(raw_alpha)
        return f"{r:02X}{g:02X}{b:02X}", max(0.0, min(1.0, alpha))
    return "000000", 0.0


def rgb_tuple(value: str) -> tuple[int, int, int]:
    hexed, _alpha = parse(value)
    return int(hexed[0:2], 16), int(hexed[2:4], 16), int(hexed[4:6], 16)


def to_hex(r: int, g: int, b: int) -> str:
    clamp = lambda n: max(0, min(255, int(round(n))))  # noqa: E731 — 一行足够清楚
    return f"{clamp(r):02X}{clamp(g):02X}{clamp(b):02X}"


def blend(fg: str, bg: str, t: float) -> str:
    """fg 往 bg 靠 t（0=fg，1=bg）。用于派生柔和底色与描边色。"""
    fr, fg_, fb = rgb_tuple(fg)
    br, bg_, bb = rgb_tuple(bg)
    t = max(0.0, min(1.0, t))
    return to_hex(fr + (br - fr) * t, fg_ + (bg_ - fg_) * t, fb + (bb - fb) * t)


def with_alpha(value: str, alpha: float) -> str:
    hexed, _ = parse(value)
    return f"#{hexed}" if alpha >= 1 else f"rgba({int(hexed[0:2], 16)},{int(hexed[2:4], 16)},{int(hexed[4:6], 16)},{alpha:.3f})"


def luminance(value: str) -> float:
    """WCAG 相对亮度。"""
    def channel(raw: int) -> float:
        c = raw / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb_tuple(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    """对比度 1~21。半透明前景先与背景混合再算（浏览器里看到的就是混合结果）。"""
    hexed, alpha = parse(fg)
    if alpha < 1:
        hexed = blend(hexed, bg, 1 - alpha)
    a, b = luminance(hexed), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(bg: str, candidates: tuple[str, ...], minimum: float = 4.5) -> str:
    """从候选色里挑第一个达标的；都不达标就挑对比度最高的那个。"""
    best, best_ratio = candidates[0], 0.0
    for candidate in candidates:
        ratio = contrast(candidate, bg)
        if ratio >= minimum:
            return candidate
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best


# ---------------------------------------------------------------- HLS
# 上传模板往往只给两个品牌色 + 一堆灰，图表要的 6~8 色带只能靠色相旋转派生；
# OOXML 的 tint/shade/lumMod 也定义在 HLS 亮度上，两处共用同一套换算。

def hls(value: str) -> tuple[float, float, float]:
    """→ (色相 0~1, 亮度 0~1, 饱和度 0~1)。"""
    r, g, b = rgb_tuple(value)
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def from_hls(h: float, lightness: float, saturation: float) -> str:
    r, g, b = colorsys.hls_to_rgb(
        h % 1.0, max(0.0, min(1.0, lightness)), max(0.0, min(1.0, saturation))
    )
    return to_hex(r * 255, g * 255, b * 255)


def hue(value: str) -> float:
    """色相，单位度（0~360）。"""
    return hls(value)[0] * 360


def lightness(value: str) -> float:
    return hls(value)[1]


def saturation(value: str) -> float:
    return hls(value)[2]


def chroma(value: str) -> float:
    """RGB 极差归一。判断「这是不是一支彩色」只能看它：
    HLS 饱和度在近白/近黑处会虚高——正文墨色 F2F5F7 的 s≈0.24，比很多品牌色还高。
    """
    r, g, b = rgb_tuple(value)
    return (max(r, g, b) - min(r, g, b)) / 255


def retone(value: str, *, light: float | None = None, sat: float | None = None) -> str:
    """保持色相，改亮度/饱和度。None = 不改。"""
    h, l, s = hls(value)
    return from_hls(h, l if light is None else light, s if sat is None else sat)


def rotate(value: str, degrees: float) -> str:
    h, l, s = hls(value)
    return from_hls(h + degrees / 360.0, l, s)


def hue_gap(a: str, b: str) -> float:
    """两个色相的圆周距离（0~180）。灰色饱和度近 0，色相无意义，调用方先过滤。"""
    return abs((hue(a) - hue(b) + 180) % 360 - 180)


def distance(a: str, b: str) -> float:
    """RGB 欧氏距离归一到 0~1，用来判「这两个采样是不是同一个颜色」。"""
    ar, ag, ab = rgb_tuple(a)
    br, bg_, bb = rgb_tuple(b)
    return (((ar - br) ** 2 + (ag - bg_) ** 2 + (ab - bb) ** 2) / (3 * 255 * 255)) ** 0.5