"""每份稿子一套视觉方向，而不是从六套预置里挑一套。

**分工**：模型（经 impeccable 技能）只决定「这份内容该是什么气质」——明暗、主色相、
饱和度、副色相、疏密、母题；代码负责把这些参数落成一套**对比度必定达标**的配色与
图表色带。让模型直接写十六进制，几乎必然写出低对比的糊色和紫蓝渐变（检测器的
头号 AI 指纹）；让它只挑方向、把物理约束留在代码里，才有既不同又不出错的结果。

方向由内容哈希决定：同一份大纲重复生成得到同一个方向（可复现、可测试），
不同大纲自然落到不同的世界（不再千篇一律）。设计台的 seed 参与哈希，等于「换一版」。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.ppt_studio.htmlkit import color, tokens as tk
from app.services.ppt_studio.outline import Outline

logger = get_logger(__name__)

MODES = ("dark", "light")
DENSITY = ("loose", "normal", "tight")
MOTIFS = ("none", "rule", "grid", "index", "glass")

# 兜底用的色相池：都是能配出干净底色、又能撑起强调色的位置；
# 刻意避开 250~320（紫）——那是检测器明令的 AI 指纹，也让兜底不至于难看。
_HUES = (8, 26, 42, 96, 138, 168, 190, 208, 226, 344)

# 内容里出现这些词时，色相/明暗有个明显更贴题的落点
_TOPICS: tuple[tuple[tuple[str, ...], int, str], ...] = (
    (("制造", "产线", "工厂", "设备", "良率", "换线", "车间"), 208, "dark"),
    (("能源", "碳", "绿色", "环保", "风电", "光伏", "排放"), 150, "light"),
    (("金融", "财务", "投资", "成本", "利润", "预算", "现金流"), 226, "dark"),
    (("医疗", "健康", "临床", "药品", "医院", "患者"), 172, "light"),
    (("教育", "培训", "课程", "学生", "校园"), 26, "light"),
    (("政府", "政策", "治理", "城市", "公共", "规划"), 214, "light"),
    (("消费", "零售", "品牌", "营销", "用户", "增长"), 344, "light"),
    (("技术", "研发", "平台", "算法", "系统", "云", "数据"), 190, "dark"),
)


@dataclass
class Direction:
    key: str
    name: str
    thesis: str
    mode: str
    hue: int
    chroma: float
    hue2: int
    density: str
    motif: str

    def payload(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "thesis": self.thesis,
            "mode": self.mode,
            "hue": self.hue,
            "chroma": round(self.chroma, 3),
            "hue2": self.hue2,
            "density": self.density,
            "motif": self.motif,
        }

    def preset(self) -> dict:
        return synthesize(self)


# ---------------------------------------------------------------- 选方向


def ask(outline: Outline, ws: str) -> Direction:
    """让模型按内容定气质；任何失败都退回内容启发式，不阻断生成。"""
    if not ws:
        return guess(outline)
    try:
        from app.services.ppt_studio.htmlkit import direction_prompt, skill_bridge

        skill = skill_bridge.resolve(ws, command="new-work")
        system = direction_prompt.compose(skill.system if skill else None)
        text = _call(system, direction_prompt.brief(outline), ws)
        direction = parse(text)
        if direction is not None:
            return direction
        logger.warning("方向解析失败，改用内容启发式")
    except Exception as exc:  # noqa: BLE001 - 选方向失败不该拖垮生成
        logger.warning("方向请求失败，改用内容启发式：%s", exc)
    return guess(outline)


def resolve_for(outline: Outline, session_id: str, ws: str, *,
                allow_llm: bool = True) -> Direction:
    """预览与生成共用同一份方向：按内容指纹取缓存，变了才重新判断。

    设计台每拖一次滑块都会调预览，不缓存就会变成一个滑块一次模型调用。
    """
    fingerprint = seed_of(outline)
    cached = _cached(session_id)
    if cached and cached.get("fingerprint") == fingerprint:
        try:
            return Direction(**cached["direction"])
        except TypeError:
            pass
    use_llm = allow_llm and _llm_enabled()
    direction = ask(outline, ws) if use_llm else guess(outline)
    _remember(session_id, fingerprint, direction)
    return direction


_CACHE_LOCK = threading.Lock()


def _llm_enabled() -> bool:
    try:
        from app.core.config import get_settings

        return bool(get_settings().ppt_auto_direction)
    except Exception:  # noqa: BLE001 - 配置读不到就按保守路径走
        return False


def _cached(session_id: str) -> dict | None:
    if not session_id:
        return None
    try:
        from app.services.ppt_studio import session_store

        session = session_store.get_session(session_id)
    except Exception:  # noqa: BLE001 - 缓存读不到就重算
        return None
    value = (session or {}).get("design_direction")
    return value if isinstance(value, dict) else None


def _remember(session_id: str, fingerprint: int, direction: Direction) -> None:
    if not session_id:
        return
    try:
        from app.services.ppt_studio import session_store

        with _CACHE_LOCK:
            session_store.update_session(session_id, {
                "design_direction": {"fingerprint": fingerprint, "direction": direction.payload()},
            })
    except Exception as exc:  # noqa: BLE001 - 只影响下一次是否复用
        logger.warning("方向缓存写入失败：%s", exc)


def _call(system: str, brief: str, ws: str) -> str:
    """一次小调用。渲染在 to_thread 的工作线程里跑，这里没有事件循环。"""
    import asyncio

    from langchain_core.messages import HumanMessage, SystemMessage

    from app.services.llm import stream_chat

    async def run() -> str:
        buf: list[str] = []
        async for kind, text in stream_chat(
            [SystemMessage(system), HumanMessage(brief)],
            temperature=0.5,
            workspace_id=ws,
        ):
            if kind in ("usage", "reasoning"):
                continue
            buf.append(text)
        return "".join(buf)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run())
    raise RuntimeError("选方向不能在事件循环线程里同步调用")


def parse(text: str) -> Direction | None:
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        mode = str(data.get("mode") or "").lower()
        density = str(data.get("density") or "").lower()
        motif = str(data.get("motif") or "").lower()
        hue = int(data.get("hue")) % 360
        hue2 = int(data.get("hue2")) % 360
        chroma = float(data.get("chroma"))
    except (TypeError, ValueError):
        return None
    if mode not in MODES or density not in DENSITY or motif not in MOTIFS:
        return None
    if not 0.10 <= chroma <= 0.52:
        return None
    # 两个色相贴太近时图表色带会糊成一片；拉开到 40 度以上
    if color.hue_gap(_hx(hue), _hx(hue2)) < 40:
        return None
    name = str(data.get("name") or "").strip()[:12]
    thesis = str(data.get("thesis") or "").strip()[:120]
    if not name:
        return None
    return Direction(
        key=_slug(name), name=name, thesis=thesis, mode=mode,
        hue=hue, chroma=chroma, hue2=hue2, density=density, motif=motif,
    )


def guess(outline: Outline) -> Direction:
    """内容启发式：先认行业词，再按内容哈希在色相池里取一个。"""
    seed = seed_of(outline)
    text = _corpus(outline)
    for words, hue, mode in _TOPICS:
        if any(word in text for word in words):
            second = _second_hue(hue, seed)
            return Direction(
                key=_slug(_TOPIC_NAMES.get(hue, "direction")),
                name=_TOPIC_NAMES.get(hue, "内敛中性"),
                thesis="按内容主题选的配色：深色压住数据，强调色只留给结论。",
                mode=mode, hue=hue, chroma=0.34, hue2=second,
                density="normal", motif=_MOTIFS[seed % len(_MOTIFS)],
            )
    hue = _HUES[seed % len(_HUES)]
    return Direction(
        key=f"neutral-{hue}",
        name="中性克制",
        thesis="内容没有明显的行业指向，用低饱和中性色配一个克制的强调色。",
        mode="dark" if seed % 3 else "light",
        hue=hue, chroma=0.28, hue2=_second_hue(hue, seed),
        density="normal", motif=_MOTIFS[seed % len(_MOTIFS)],
    )


_TOPIC_NAMES = {
    208: "冷轧钢", 150: "常青", 226: "深海账本", 172: "临床青",
    26: "晨光", 214: "政务蓝", 344: "橱窗", 190: "机读蓝",
}
_MOTIFS = ("rule", "grid", "index", "glass", "none")


def seed_of(outline: Outline) -> int:
    """内容指纹：同一份大纲 → 同一方向；不同大纲 → 不同世界。"""
    corpus = _corpus(outline) + "|" + str(getattr(outline.style, "seed", 0) or 0)
    return int(hashlib.sha256(corpus.encode("utf-8")).hexdigest()[:8], 16)


def _corpus(outline: Outline) -> str:
    parts = [outline.title or "", outline.subtitle or "", outline.audience or ""]
    for section in outline.sections:
        parts.append(section.title or "")
        for page in section.pages:
            parts.append(page.title or "")
            parts.extend(page.bullets or [])
    return "\n".join(parts)


def _second_hue(hue: int, seed: int) -> int:
    """副色相：错开 55° 起，且与主色相差不小于 40°，保证图表第一二色分得开。"""
    candidate = _avoid_purple(int((hue + 55 + (seed % 5) * 22) % 360))
    gap = abs(candidate - hue) % 360
    if min(gap, 360 - gap) < 40:
        candidate = _avoid_purple((candidate + 120) % 360)
    return candidate


def _avoid_purple(hue: int) -> int:
    """紫色系（250~320）是检测器明令的 AI 指纹，派生的色相一律绕开。"""
    hue %= 360
    if 250 <= hue <= 320:
        # 移到禁令带外最近的一侧：偏蓝的去 240，偏红的去 330
        return 240 if hue - 250 <= 320 - hue else 330
    return hue


def _slug(name: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if ascii_part:
        return ascii_part
    # 中文名没有 ascii 部分：用稳定哈希，不能用内置 hash()（每个进程随机化）
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return f"d{int(digest[:6], 16) % 100000}"


def _hx(hue: int) -> str:
    return color.from_hls((hue % 360) / 360.0, 0.5, 0.6)


# ---------------------------------------------------------------- 落成配色


def synthesize(direction: Direction) -> dict:
    """方向 → 完整 preset。所有文字/底色配比在这里就地保证达标。

    不做「先配色再指望检测器兜底」：这里选的是亮度区间，保证 bg↔ink ≥ 10:1、
    muted ≥ 4.5:1、图表色 ≥ 3:1；检测器只当复核，不是唯一防线。
    """
    h, h2, sat = direction.hue % 360, direction.hue2 % 360, direction.chroma
    dark = direction.mode == "dark"

    if dark:
        bg = color.from_hls(h / 360.0, 0.072, sat * 0.26)
        bg_soft = color.from_hls(h / 360.0, 0.132, sat * 0.34)
        surface = color.from_hls(h / 360.0, 0.182, sat * 0.38)
        paper = "#FFFFFF"
        ink = color.from_hls(h / 360.0, 0.128, sat * 0.30)
        on_dark = color.readable_on(bg, (color.from_hls(h / 360.0, 0.975, 0.08), "#FFFFFF"), 12.0)
        on_dark_muted = color.readable_on(
            bg,
            (color.from_hls(h / 360.0, 0.70, sat * 0.28), color.from_hls(h / 360.0, 0.78, sat * 0.22),
             color.from_hls(h / 360.0, 0.86, sat * 0.16)),
            4.5,
        )
        ink_muted = color.readable_on("#FFFFFF", (color.from_hls(h / 360.0, 0.45, sat * 0.22),), 4.5)
        accent = color.from_hls(h / 360.0, 0.58, sat)
        accent2 = color.from_hls(h2 / 360.0, 0.60, sat * 0.92)
    else:
        paper = color.from_hls(h / 360.0, 0.982, sat * 0.12)
        surface = color.from_hls(h / 360.0, 0.952, sat * 0.16)
        bg = paper
        bg_soft = surface
        ink = color.readable_on(paper, (color.from_hls(h / 360.0, 0.135, sat * 0.36),), 12.0)
        ink_muted = color.readable_on(
            paper,
            (color.from_hls(h / 360.0, 0.44, sat * 0.24), color.from_hls(h / 360.0, 0.38, sat * 0.26),
             color.from_hls(h / 360.0, 0.32, sat * 0.28)),
            4.5,
        )
        on_dark = color.from_hls(h / 360.0, 0.97, 0.06)
        on_dark_muted = color.readable_on("#0B1020", (color.from_hls(h / 360.0, 0.70, sat * 0.3),), 4.5)
        accent = color.from_hls(h / 360.0, 0.44, sat)
        accent2 = color.from_hls(h2 / 360.0, 0.45, sat * 0.92)

    card = color.blend(surface, bg, 0.25) if dark else paper
    chart = _chart(h, h2, sat, card, dark)
    stops = _cover_stops(bg, accent, accent2, dark)

    palette = {
        "bg": bg, "bg_soft": bg_soft, "paper": paper, "surface": surface,
        "line": color.blend(on_dark if dark else ink, bg, 0.86 if dark else 0.80),
        "ink": ink, "ink_muted": ink_muted,
        "on_dark": on_dark, "on_dark_muted": on_dark_muted,
        "accent": accent,
        "accent_soft": color.blend(accent, bg, 0.80 if dark else 0.86),
        "accent2": accent2,
    }
    return {
        "key": f"auto:{direction.key}",
        "name": direction.name,
        "tagline": direction.thesis[:28],
        "font": tk.FONT_SANS,
        "palette": palette,
        "family": {
            "key": direction.key,
            "mode": direction.mode,
            "motif": direction.motif,
            "display_font": tk.FONT_LATIN,
            "body_font": tk.FONT_SANS,
            "cover_stops": stops,
            "chart": chart,
            "grain": 0.04 if dark else 0.0,
        },
        "margin_px": {"loose": 76, "normal": 64, "tight": 52}[direction.density],
        "direction": direction.payload(),
    }


def _chart(h: int, h2: int, sat: float, card: str, dark: bool) -> tuple[str, ...]:
    """图表色带：以色相环错开，逐档压到卡片上 ≥3:1。

    第一二是主副色相，后面按固定角度铺开；末位是中性灰，给「其他/合计」用。
    """
    towards = "FFFFFF" if dark else "000000"
    offsets = (0, None, 38, -30, 74, -62, 0)  # None = 用副色相
    out: list[str] = []
    for index, offset in enumerate(offsets):
        if offset is None:
            raw = color.from_hls(h2 / 360.0, 0.56, sat * 0.95)
        elif index == len(offsets) - 1:
            raw = color.from_hls(h / 360.0, 0.62, 0.06)
        else:
            raw = color.from_hls(_avoid_purple(h + offset) / 360.0, 0.56, sat * (0.95 if index % 2 else 0.85))
        out.append(color.readable_on(
            card,
            (raw, color.blend(raw, towards, 0.22), color.blend(raw, towards, 0.42),
             color.blend(raw, towards, 0.60)),
            3.0,
        ))
    return tuple(out)


def _cover_stops(bg: str, accent: str, accent2: str, dark: bool) -> tuple[str, ...]:
    """封面渐变：同底色出发，走到主/副色相。每份稿子的封面因此不一样。"""
    if dark:
        return (
            color.blend(bg, "000000", 0.45),
            bg,
            color.blend(bg, accent, 0.34),
            color.blend(bg, accent2, 0.42),
        )
    return (
        color.blend(bg, "FFFFFF", 0.6),
        bg,
        color.blend(bg, accent, 0.10),
        color.blend(bg, accent2, 0.18),
    )


def contrast_report(tokens: dict) -> dict:
    """把关键配比量出来，供测试与日志用。"""
    colors = tokens["colors"]
    bg = colors["bg"]
    card = colors["card"]
    return {
        "ink": round(color.contrast(colors["ink"], bg), 2),
        "muted": round(color.contrast(colors["muted"], bg), 2),
        "faint": round(color.contrast(colors["faint"], bg), 2),
        "accent_ink": round(color.contrast(colors["accent_ink"], bg), 2),
        "card_ink": round(color.contrast(colors["ink"], card), 2),
        "chart_min": round(min(color.contrast(hue, card) for hue in tokens["chart"]), 2),
    }


__all__ = [
    "Direction", "ask", "guess", "parse", "seed_of", "synthesize", "contrast_report",
    "resolve_for",
    "MODES", "DENSITY", "MOTIFS",
]