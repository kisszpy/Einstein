"""上传模板 → 设计家族：把公司 pptx 的美术方向抽成 HTML 引擎能直接吃的 preset。

为什么不「反用模板自身」当基底：占位符填空出来的稿子只能继承母版的框，
版式仍是十年前的项目符号列表。这里走的是风格迁移——量出模板真正在用的
底色 / 品牌色 / 字体 / 版心 / 圆角 / 页面比例，交给 htmlkit 重绘每一页，
成稿既是模板的气质，又是新引擎的版面。

采样口径（三条都踩过坑，别改回「想当然」的写法）：
- 底色只看母版/版式/前几页的 `p:bg` 与满版矩形，图片底用 Pillow 取均值；
  明暗按**内页**权重判定——封面深、内页浅的公司模板不在少数，多数页说了算。
- 品牌色按 面积 × 角色权重 累计，滤掉近中性灰与底色本身，再按色相聚类；
  主题 accent1..6 给一份底权重，保证「只有主题没有色块」的模板也抽得出主色。
- 字体只写本机真装了的：DOM 实测行宽与 pptx 断行必须用同一份字库，
  写一个没装的名字会让实测结果与成稿错位，所以替换必须留痕。
"""

from __future__ import annotations

import io
import os
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import median

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

from app.core.logging import get_logger
from app.services.ppt_studio import designs
from app.services.ppt_studio.htmlkit import color
from app.services.ppt_studio.htmlkit import tokens as tk

logger = get_logger(__name__)

# 抽取口径版本：改了算法就 +1，已入库的包在 style_of 里按版本号自动重抽
STYLE_VERSION = 2

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_EMU_IN = 914400.0

# 明暗分界：WCAG 相对亮度 0.35 大致对应 #9A9A9A 以上的浅底
_DARK_BELOW = 0.35
# 饱和度低于此值算中性灰，不参与品牌色竞选（公司模板里灰块比色块多得多）
_CHROMA_MIN = 0.14
# 真实色差门槛 + 明度带：只有 HLS 饱和度会把正文墨色（F2F5F7，s≈0.24）当成品牌色
_RGB_CHROMA_MIN = 0.12
_INK_BAND = (0.06, 0.94)
# 采样页数上限：模板动辄几十页示例，全量扫既慢又会让示例页压过母版
_MAX_SLIDES = 8

# 主题色名 → 色板键（bg1/tx1 是 OOXML 的别名，直接查表会漏）
_SCHEME_ALIAS = {
    "bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2", "phClr": "accent1",
}

# 只输出这张表里的字体：值是本机字库文件名，任一存在即视为可用。
# 表外的名字一律替换并留痕——写没装的字体会让实测与成稿断行不一致。
_FONT_FILES: dict[str, tuple[str, ...]] = {
    "Microsoft YaHei": ("msyh.ttc", "msyh.ttf"),
    "DengXian": ("Deng.ttf", "DengXian.ttf"),
    "SimSun": ("simsun.ttc", "simsun.ttf"),
    "SimHei": ("simhei.ttf",),
    "KaiTi": ("simkai.ttf",),
    "FangSong": ("simfang.ttf",),
    "Noto Sans SC": ("NotoSansSC-VF.ttf", "NotoSansSC-Regular.otf"),
    "Noto Serif SC": ("NotoSerifSC-VF.ttf", "NotoSerifSC-Regular.otf"),
    "Inter": ("Inter-Regular.ttf", "Inter.ttf"),
    "Arial": ("arial.ttf",),
    "Calibri": ("calibri.ttf",),
    "Calibri Light": ("calibril.ttf",),
    "Georgia": ("georgia.ttf",),
    "Times New Roman": ("times.ttf",),
    "Segoe UI": ("segoeui.ttf",),
    "Consolas": ("consola.ttf",),
}

# 中文字体名 → 拉丁族名（模板里「微软雅黑」和「Microsoft YaHei」是同一个字体）
_FONT_ALIAS = {
    "微软雅黑": "Microsoft YaHei", "微軟雅黑": "Microsoft YaHei",
    "微软雅黑 light": "Microsoft YaHei", "microsoft yahei ui": "Microsoft YaHei",
    "等线": "DengXian", "等线 light": "DengXian", "dengxian light": "DengXian",
    "宋体": "SimSun", "新宋体": "SimSun", "nsimsun": "SimSun",
    "黑体": "SimHei", "楷体": "KaiTi", "楷体_gb2312": "KaiTi", "仿宋": "FangSong",
    "仿宋_gb2312": "FangSong",
    "思源黑体": "Noto Sans SC", "思源黑体 cn": "Noto Sans SC", "思源黑体 sc": "Noto Sans SC",
    "source han sans sc": "Noto Sans SC", "source han sans cn": "Noto Sans SC",
    "noto sans cjk sc": "Noto Sans SC",
    "思源宋体": "Noto Serif SC", "思源宋体 cn": "Noto Serif SC",
    "source han serif sc": "Noto Serif SC", "noto serif cjk sc": "Noto Serif SC",
    "times": "Times New Roman", "helvetica": "Arial", "helvetica neue": "Arial",
    "arial mt": "Arial", "segoe ui light": "Segoe UI", "segoe ui semibold": "Segoe UI",
}

_CJK_FONTS = {
    "Microsoft YaHei", "DengXian", "SimSun", "SimHei", "KaiTi", "FangSong",
    "Noto Sans SC", "Noto Serif SC",
}
_SERIF_FONTS = {"SimSun", "KaiTi", "FangSong", "Noto Serif SC", "Georgia", "Times New Roman"}


# ---------------------------------------------------------------- 字体可用性

@lru_cache(maxsize=1)
def _font_files() -> frozenset[str]:
    """本机字库文件名（小写）。一次目录列举，进程内缓存。"""
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    names: set[str] = set()
    for directory in dirs:
        try:
            names.update(p.name.lower() for p in directory.iterdir() if p.is_file())
        except OSError:
            continue
    return frozenset(names)


def _font_installed(name: str) -> bool:
    files = _FONT_FILES.get(name)
    return bool(files) and any(f.lower() in _font_files() for f in files)


def _canonical_font(raw: str) -> str:
    """模板里的字体名 → 本机族名；认不出返回 ""。"""
    text = (raw or "").strip()
    if not text or text.startswith("+"):  # +mj-lt / +mn-ea 是主题占位，不是字体名
        return ""
    key = text.lower()
    if key in _FONT_ALIAS:
        return _FONT_ALIAS[key]
    for name in _FONT_FILES:
        if name.lower() == key:
            return name
    return ""


def _resolve_font(raw: str, *, cjk: bool, fallback: str) -> tuple[str, str]:
    """→ (实际使用的字体, 降级说明或空串)。"""
    original = (raw or "").strip()
    name = _canonical_font(original)
    if name and _font_installed(name) and (name in _CJK_FONTS or not cjk):
        return name, ""
    reason = "本机未安装" if not name or not _font_installed(name) else "不含中文字形"
    note = f"模板字体「{original or '未指定'}」{reason}，已替换为 {fallback}"
    return fallback, note


# ---------------------------------------------------------------- 颜色读取

def _apply_transforms(hexed: str, node) -> str:
    """OOXML 颜色变换：tint/shade 定义在 HLS 亮度上，lumMod/satMod 同理。"""
    h, light, sat = color.hls(hexed)
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        try:
            amount = float(child.get("val", "100000")) / 100000.0
        except (TypeError, ValueError):
            continue
        if tag == "tint":
            light = light * amount + (1 - amount)
        elif tag == "shade":
            light = light * amount
        elif tag == "lumMod":
            light = light * amount
        elif tag == "lumOff":
            light = light + amount
        elif tag == "satMod":
            sat = sat * amount
        elif tag == "satOff":
            sat = sat + amount
    return color.from_hls(h, light, sat)


def _clr(node, palette: dict[str, str]) -> str:
    """a:srgbClr / a:schemeClr / a:sysClr → 裸 hex（认不出返回 ""）。"""
    if node is None:
        return ""
    tag = node.tag.rsplit("}", 1)[-1]
    if tag == "srgbClr":
        base = (node.get("val") or "").strip().lstrip("#").upper()
        if len(base) != 6:
            return ""
    elif tag == "schemeClr":
        key = node.get("val") or ""
        base = palette.get(_SCHEME_ALIAS.get(key, key), "")
        if not base:
            return ""
    elif tag == "sysClr":
        base = (node.get("lastClr") or "").strip().upper()
        if len(base) != 6:
            return ""
    else:
        return ""
    return _apply_transforms(base, node)


def _solid(container, palette: dict[str, str]) -> str:
    """容器下的 a:solidFill → 裸 hex。"""
    if container is None:
        return ""
    fill = container.find(qn("a:solidFill"))
    if fill is None or not len(fill):
        return ""
    return _clr(fill[0], palette)


def _gradient(container, palette: dict[str, str]) -> list[tuple[float, str]]:
    if container is None:
        return []
    fill = container.find(qn("a:gradFill"))
    if fill is None:
        return []
    stops: list[tuple[float, str]] = []
    for gs in fill.iter(qn("a:gs")):
        if not len(gs):
            continue
        hexed = _clr(gs[0], palette)
        if not hexed:
            continue
        try:
            pos = float(gs.get("pos", "0")) / 100000.0
        except (TypeError, ValueError):
            pos = 0.0
        stops.append((pos, hexed))
    stops.sort(key=lambda item: item[0])
    return stops


def _blip_average(container, part, cache: dict[str, str]) -> str:
    """图片底/满版图 → 平均色。Pillow 缺失或图损坏时返回 ""，不阻塞抽取。"""
    if container is None or part is None:
        return ""
    blip = container.find(f".//{_q('a:blip')}")
    if blip is None:
        return ""
    rid = blip.get(f"{{{_R}}}embed") or blip.get(f"{{{_R}}}link") or ""
    if not rid:
        return ""
    if rid in cache:
        return cache[rid]
    hexed = ""
    try:
        from PIL import Image  # 局部导入：无 Pillow 的环境只是少一路采样

        blob = part.rels[rid].target_part.blob
        with Image.open(io.BytesIO(blob)) as img:
            tiny = img.convert("RGB").resize((24, 24))
            pixels = list(tiny.getdata())
        r = sum(p[0] for p in pixels) / len(pixels)
        g = sum(p[1] for p in pixels) / len(pixels)
        b = sum(p[2] for p in pixels) / len(pixels)
        hexed = color.to_hex(r, g, b)
    except Exception:  # noqa: BLE001 — 图片可能是 wmf/emf 或外链，取不到就跳过
        hexed = ""
    cache[rid] = hexed
    return hexed


def _q(tag: str) -> str:
    prefix, local = tag.split(":", 1)
    ns = {"a": _A, "p": _P, "r": _R}[prefix]
    return f"{{{ns}}}{local}"


# ---------------------------------------------------------------- 采样

def _walk(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(shape.shapes)


def _bg_of(element, palette: dict[str, str], part, cache: dict[str, str]) -> tuple[str, list[str]]:
    """p:cSld/p:bg → (主底色, 渐变停靠点)。"""
    bg = element.find(f"{_q('p:cSld')}/{_q('p:bg')}")
    if bg is None:
        return "", []
    props = bg.find(_q("p:bgPr"))
    if props is None:
        # bgRef 指向主题背景填充样式，idx=1001 之类；取其中的颜色引用兜底
        ref = bg.find(_q("p:bgRef"))
        return (_clr(ref[0], palette) if ref is not None and len(ref) else ""), []
    solid = _solid(props, palette)
    if solid:
        return solid, []
    stops = _gradient(props, palette)
    if stops:
        return stops[len(stops) // 2][1], [hexed for _pos, hexed in stops]
    return _blip_average(props.find(_q("a:blipFill")), part, cache), []


def _geometry(shape) -> tuple[float, float]:
    """→ (w_px, h_px)；缺继承坐标的占位符按 0 处理（不参与采样）。"""
    scale = tk.PX_PER_INCH / _EMU_IN
    try:
        return (shape.width or 0) * scale, (shape.height or 0) * scale
    except (AttributeError, ValueError):
        return 0.0, 0.0


class _Samples:
    """一次抽取过程里累计的所有证据。"""

    def __init__(self) -> None:
        # (hex, weight, role)：role 分 cover/content/master，明暗只由内页证据决定
        self.backgrounds: list[tuple[str, float, str]] = []
        self.cover_stops: list[str] = []
        self.fills: dict[str, float] = defaultdict(float)
        self.round_radii: list[float] = []
        self.card_shapes = 0
        self.hairlines = 0
        self.big_ordinals = 0
        # 真实模板多半把字体写在 run 上而不是主题里，只读主题会漏掉它实际用的字族
        self.font_names: dict[str, dict[str, float]] = {
            "ea": defaultdict(float), "latin": defaultdict(float), "title": defaultdict(float),
        }
        self.cjk_chars = 0
        self.latin_chars = 0
        self.slide_text_lefts: list[float] = []
        self.blip_cache: dict[str, str] = {}


def _sample_surface(element, part, palette, samples: _Samples, weight: float, role: str) -> None:
    hexed, stops = _bg_of(element, palette, part, samples.blip_cache)
    if hexed:
        samples.backgrounds.append((hexed, weight, role))
        if stops and role == "cover" and not samples.cover_stops:
            samples.cover_stops = stops


def _sample_shapes(shapes, part, palette, samples: _Samples, weight: float, role: str,
                   slide_w: int, slide_h: int, *, source: str = "layout") -> None:
    px_area = max(1.0, float(slide_w) * float(slide_h) * (tk.PX_PER_INCH / _EMU_IN) ** 2)
    canvas_w = float(slide_w) * tk.PX_PER_INCH / _EMU_IN
    for shape in _walk(shapes):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            continue  # 组自身没有 spPr，子形状已单独遍历，再 iter 文本会重复计权
        element = shape._element
        sp_pr = element.find(_q("p:spPr"))
        width, height = _geometry(shape)
        left = float(shape.left or 0) * tk.PX_PER_INCH / _EMU_IN
        ratio = min(1.0, (width * height) / px_area)
        if ratio >= 0.8:
            # 满版矩形/满版图就是这页真正的底色，比 p:bg 更可信
            solid = _solid(sp_pr, palette)
            stops = _gradient(sp_pr, palette)
            if solid:
                samples.backgrounds.append((solid, weight * 1.2, role))
            elif stops:
                samples.backgrounds.append((stops[len(stops) // 2][1], weight * 1.2, role))
                if role == "cover" and not samples.cover_stops:
                    samples.cover_stops = [h for _p, h in stops]
            else:
                avg = _blip_average(element.find(_q("p:blipFill")), part, samples.blip_cache)
                if avg:
                    samples.backgrounds.append((avg, weight, role))

        painted = ""
        if sp_pr is not None and 0.001 < ratio < 0.8:
            painted = _solid(sp_pr, palette)
            if painted:
                # sqrt：大色带该占优势，但别让一条横幅把其余证据全压没
                samples.fills[painted] += weight * (ratio ** 0.5)
            # 只有「自己上了色」的块才算设计卡片：空占位符不是卡片，
            # 否则 python-pptx 自带的十几个版式能把任何模板都推成玻璃拟态。
            if painted and 0.03 <= ratio <= 0.45:
                samples.card_shapes += 1
                radius = _round_radius(sp_pr, width, height)
                if radius is not None:
                    samples.round_radii.append(radius)
            if min(width, height) <= 4.0 and max(width, height) >= 60.0:
                samples.hairlines += 1

        # 文字证据只认成稿页：母版/版式里「Click to edit Master title style」这类
        # 占位提示语会上千个拉丁字符 + 44pt 字号，把真实正文的字体和大序号统计全带偏。
        has_text = False
        if source == "slide":
            for node in element.iter(qn("a:t")):
                for char in node.text or "":
                    if "\u4e00" <= char <= "\u9fff":
                        samples.cjk_chars += 1
                        has_text = True
                    elif char.isascii() and char.isalpha():
                        samples.latin_chars += 1
                        has_text = True
        if (source == "slide" and role == "content" and has_text
                and width >= canvas_w * 0.25 and 8 <= left <= canvas_w * 0.2):
            samples.slide_text_lefts.append(left)

        for rpr in element.iter(qn("a:rPr")):
            ink = _solid(rpr, palette)
            if ink:
                samples.fills[ink] += weight * 0.06
            if source != "slide":
                continue
            size = rpr.get("sz")
            points = int(size) if size and size.isdigit() else 1800
            if points >= 4000:
                samples.big_ordinals += 1
            latin = rpr.find(qn("a:latin"))
            ea = rpr.find(qn("a:ea"))
            latin_name = (latin.get("typeface") if latin is not None else "") or ""
            ea_name = (ea.get("typeface") if ea is not None else "") or ""
            # 大字号是标题，小字号才是正文：正文族要由正文说话
            bucket = "title" if points >= 2400 else "latin"
            for name, key in ((latin_name, bucket), (ea_name, "title" if points >= 2400 else "ea")):
                if name and not name.startswith("+"):
                    samples.font_names[key][name] += weight


def _round_radius(sp_pr, width: float, height: float) -> float | None:
    """roundRect 的 adj（默认 16667 = 短边的 1/6）→ 像素圆角。"""
    geom = sp_pr.find(qn("a:prstGeom"))
    if geom is None or geom.get("prst") not in ("roundRect", "round1Rect", "round2SameRect"):
        return None
    adj = 16667.0
    for gd in geom.iter(qn("a:gd")):
        if gd.get("name") in ("adj", "adj1"):
            formula = (gd.get("fmla") or "").strip()
            if formula.startswith("val "):
                try:
                    adj = float(formula[4:])
                except ValueError:
                    pass
            break
    return min(width, height) * max(0.0, min(50000.0, adj)) / 100000.0


# ---------------------------------------------------------------- 决策

def _decide_mode(samples: _Samples, palette: dict[str, str]) -> tuple[str, str, str]:
    """→ (mode, 内容底色, 封面底色)。

    封面不参与明暗投票：一张深封面 + 二十页浅内页是常见做法，
    让封面那 3.0 的权重压过内页会把整份稿子刷成深色，与模板气质相反。
    """
    inner = [(h, w) for h, w, role in samples.backgrounds if role != "cover"]
    pool = inner or [(h, w) for h, w, role in samples.backgrounds]
    if not pool:
        base = palette.get("lt1") or "FFFFFF"
        mode = "dark" if color.luminance(base) < _DARK_BELOW else "light"
        return mode, base, base
    dark = sum(w for h, w in pool if color.luminance(h) < _DARK_BELOW)
    light = sum(w for h, w in pool if color.luminance(h) >= _DARK_BELOW)
    mode = "dark" if dark > light else "light"
    same = [(h, w) for h, w in pool if (color.luminance(h) < _DARK_BELOW) == (mode == "dark")]
    # 权重相同取更「极端」的那个：深色更深、浅色更浅，避免选到一块中间灰
    same.sort(key=lambda item: (-item[1], color.luminance(item[0]) if mode == "dark" else -color.luminance(item[0])))
    bg = same[0][0]
    covers = sorted(
        [(h, w) for h, w, role in samples.backgrounds if role == "cover"], key=lambda item: -item[1]
    )
    return mode, bg, (covers[0][0] if covers else bg)


def _seed_fills(samples: _Samples, palette: dict[str, str], bg: str) -> dict[str, float]:
    """主题 accent 给底权重：只有主题、没有色块的模板也要抽得出品牌色。

    但模板自己画出来的彩色一旦成立，主题就要让位——绝大多数公司模板只改版面、
    不动主题，accent1 还是 Office 出厂的那支 4F81BD，让它盖过真正的品牌副色
    等于把用户的配色改掉了。
    """
    seeds: dict[str, float] = defaultdict(float, samples.fills)
    sampled = sum(w for h, w in samples.fills.items() if _chromatic(h, bg))
    scale = 0.2 if sampled >= 0.15 else 1.0
    for index, weight in ((1, 0.55), (2, 0.32), (3, 0.18), (4, 0.18), (5, 0.18), (6, 0.18)):
        hexed = palette.get(f"accent{index}") or ""
        if hexed:
            seeds[hexed] += weight * scale
    return seeds


def _chromatic(hexed: str, bg: str) -> bool:
    """够彩、不是墨色/纸色、且和底色拉得开——三条都满足才算一支品牌色候选。"""
    light = color.lightness(hexed)
    return (
        color.chroma(hexed) >= _RGB_CHROMA_MIN
        and color.saturation(hexed) >= _CHROMA_MIN
        and _INK_BAND[0] <= light <= _INK_BAND[1]
        and color.distance(hexed, bg) >= 0.12
    )


def _pick_accents(samples: _Samples, palette: dict[str, str], bg: str) -> tuple[str, str, list[str]]:
    """→ (主色, 副色, 按权重排序的候选色)。聚类后取权重最高的两簇。"""
    seeds = _seed_fills(samples, palette, bg)
    pool = [(h, w) for h, w in seeds.items() if _chromatic(h, bg)]
    if not pool:
        # 整份模板全是灰：用主题 dk2 的色相造一个，实在没色相就用内置强调色
        for key in ("accent1", "dk2", "hlink"):
            candidate = palette.get(key) or ""
            if candidate and color.chroma(candidate) >= 0.06:
                return color.retone(candidate, sat=0.62, light=0.55), "", []
        fallback = designs.default_preset()["palette"]["accent"]
        return fallback, "", []
    pool.sort(key=lambda item: -item[1])

    clusters: list[dict] = []
    for hexed, weight in pool:
        hit = next(
            (c for c in clusters
             if color.hue_gap(c["hex"], hexed) < 20 and abs(color.lightness(c["hex"]) - color.lightness(hexed)) < 0.28),
            None,
        )
        if hit:
            hit["weight"] += weight
            hit["members"].append((hexed, weight))
        else:
            clusters.append({"hex": hexed, "weight": weight, "members": [(hexed, weight)]})
    clusters.sort(key=lambda c: -c["weight"])

    def representative(cluster: dict) -> str:
        # 取簇内权重最高的原色，不做平均：品牌色要一模一样，混出来的是第三个颜色。
        # 同权重时取饱和度更高的——那更像刻意用的品牌色，而不是随手填的浅底。
        return max(cluster["members"], key=lambda m: (m[1], color.saturation(m[0])))[0]

    primary = representative(clusters[0])
    secondary = ""
    for cluster in clusters[1:]:
        candidate = representative(cluster)
        if color.hue_gap(primary, candidate) >= 25:
            secondary = candidate
            break
    ranked = [representative(c) for c in clusters]
    return primary, secondary, ranked


def _chart_ramp(primary: str, secondary: str, ranked: list[str], mode: str) -> list[str]:
    """7 色带：色相拉开 + 明度错开，深底提亮、浅底压深，末位留一支中性色。"""
    dark = mode == "dark"
    floor, ceil = (0.44, 0.78) if dark else (0.26, 0.60)

    def tuned(hexed: str, index: int) -> str:
        light = color.lightness(hexed)
        light = max(floor, min(ceil, light))
        # 相邻两支再错开一点明度：只靠色相在投影仪上分不开
        light = max(floor, min(ceil, light + (0.05 if index % 2 else -0.03)))
        sat = max(0.34, min(0.88, color.saturation(hexed)))
        return color.retone(hexed, light=light, sat=sat)

    ramp: list[str] = []

    def push(hexed: str) -> None:
        if not hexed:
            return
        candidate = tuned(hexed, len(ramp))
        if any(color.hue_gap(candidate, have) < 20 and abs(color.lightness(candidate) - color.lightness(have)) < 0.12
               for have in ramp):
            return
        ramp.append(candidate)

    push(primary)
    push(secondary)
    for hexed in ranked[1:]:
        if len(ramp) >= 6:
            break
        push(hexed)
    # 企业模板常只有 1~2 个品牌色，剩下的按色相环补，步长避开 180°（会撞成补色对）
    for step in (152, 76, 232, 116, 300, 44):
        if len(ramp) >= 6:
            break
        push(color.rotate(primary, step))
    ramp.append(_neutral_slot(ramp, dark))
    return ramp[:7]


def _neutral_slot(ramp: list[str], dark: bool) -> str:
    """第 7 支中性色：色相已无意义，只能靠明度与前面 6 支拉开，取最小差距最大的位置。"""
    lights = [color.lightness(h) for h in ramp]
    lo, hi = (0.36, 0.90) if dark else (0.18, 0.74)
    candidates = [lo + (hi - lo) * i / 40 for i in range(41)]
    best = max(candidates, key=lambda light: min((abs(light - have) for have in lights), default=1.0))
    return color.from_hls(0.6, best, 0.05)


def _cover_stops(samples: _Samples, bg: str, paper: str, primary: str, mode: str) -> list[str]:
    """封面渐变：模板自带渐变优先照搬，但明暗必须跟成稿基调一致。

    深封面 + 浅内页的公司模板不在少数。基调由内页定，照搬深封面就会把浅色墨字
    放到深底上——实测标题对比度 1.0:1，等于看不见。基调不符时按模板自己的品牌色
    重推一段封面渐变，保住色相气质，不保住那层深底。
    """
    seeded = [h for h in samples.cover_stops if h]
    if len(seeded) >= 2:
        mean = sum(color.luminance(h) for h in seeded) / len(seeded)
        if (mean < _DARK_BELOW) == (mode == "dark"):
            return _cross_hue(seeded[:4], mode)
    if mode == "dark":
        stops = [
            color.blend(bg, "000000", 0.5),
            bg,
            color.blend(bg, primary, 0.42),
            color.blend(bg, primary, 0.66),
        ]
    else:
        stops = [
            paper,
            color.blend(paper, primary, 0.06),
            color.blend(paper, primary, 0.17),
            color.blend(paper, primary, 0.32),
        ]
    return stops


def _cross_hue(stops: list[str], mode: str) -> list[str]:
    """同色相明暗渐变不算渐变：收尾补一段真跨色相的偏移。"""
    hues = {round(color.hue(h) / 30) for h in stops if color.saturation(h) >= 0.1}
    if len(hues) >= 2 or len(stops) < 2:
        return stops
    anchor = max(stops, key=color.saturation)
    return stops[:-1] + [color.rotate(anchor, 34 if mode == "dark" else -30)]


def _palette(mode: str, bg: str, primary: str, secondary: str) -> dict[str, str]:
    """12 键色板：明暗两套一次配齐，切换 mode 时不会缺键。"""
    base_hue = color.hue(bg) if color.saturation(bg) >= 0.05 else color.hue(primary)
    if mode == "dark":
        ink = color.from_hls(base_hue / 360, 0.10, 0.28)
        on_dark = color.blend("FFFFFF", bg, 0.04)
        muted = color.readable_on(
            bg,
            (color.blend(on_dark, bg, 0.40), color.blend(on_dark, bg, 0.28), on_dark),
            4.5,
        )
        paper = "FFFFFF"
        surface = color.blend(paper, primary, 0.06)
        bg_soft = color.retone(bg, light=min(0.30, color.lightness(bg) + 0.07))
        light_ink = color.readable_on(paper, (ink, color.retone(ink, light=0.06)), 7.0)
        return {
            "bg": bg, "bg_soft": bg_soft, "paper": paper, "surface": surface,
            "line": color.blend(light_ink, paper, 0.86),
            "ink": light_ink,
            "ink_muted": color.readable_on(paper, (color.retone(ink, light=0.42), color.retone(ink, light=0.34)), 4.5),
            "on_dark": on_dark, "on_dark_muted": muted,
            "accent": primary,
            "accent_soft": color.blend(primary, paper, 0.72),
            "accent2": secondary,
        }
    paper = color.blend(bg, "FFFFFF", 0.5)
    ink = color.readable_on(
        paper,
        (color.from_hls(base_hue / 360, 0.12, 0.24), color.from_hls(base_hue / 360, 0.08, 0.20)),
        7.0,
    )
    muted = color.readable_on(
        paper,
        (color.from_hls(base_hue / 360, 0.44, 0.14), color.from_hls(base_hue / 360, 0.36, 0.16)),
        4.5,
    )
    return {
        "bg": bg,
        "bg_soft": color.blend(bg, ink, 0.07),
        "paper": paper,
        "surface": color.blend(bg, ink, 0.05),
        "line": color.blend(ink, paper, 0.84),
        "ink": ink, "ink_muted": muted,
        # 浅色家族里「深底文字」其实就是正文色，ivory 也是这么配的
        "on_dark": ink, "on_dark_muted": muted,
        "accent": primary,
        "accent_soft": color.blend(primary, paper, 0.76),
        "accent2": secondary,
    }


def _motif(samples: _Samples, mode: str, display_font: str) -> str:
    if samples.card_shapes >= 4 and len(samples.round_radii) >= 2:
        return "glass"
    if samples.big_ordinals >= 3:
        return "index"
    if samples.hairlines >= 6:
        return "rule"
    if display_font in _SERIF_FONTS:
        return "grid"
    return "rule" if mode == "dark" else "grid"


def _dominant(bucket: dict[str, float]) -> str:
    return max(bucket.items(), key=lambda item: item[1])[0] if bucket else ""


def _body_font(samples: _Samples, fonts: dict[str, str]) -> str:
    """正文字族：run 上的中文字体 > 中文稿里唯一设过的拉丁字体 > 主题 minor。"""
    sampled = _dominant(samples.font_names["ea"])
    if sampled:
        return sampled
    latin = _dominant(samples.font_names["latin"])
    if latin and samples.cjk_chars > samples.latin_chars:
        # 通篇中文却只设了 latin：作者的意思就是这一支包全部字形
        return latin
    return fonts.get("minor_ea") or fonts.get("major_ea") or ""


def _display_font(samples: _Samples, fonts: dict[str, str]) -> str:
    return (
        fonts.get("major_latin")
        or _dominant(samples.font_names["title"])
        or fonts.get("major_ea")
        or ""
    )


def _content_margin(layouts: list[dict], canvas_w: int, slide_edges: list[float] | None = None) -> int:
    """版心外边距（px）。优先量成稿页里正文框的左边界——模板自带的十几个默认版式
    往往一个都没用上，拿它们的中位数会把版心算到模板实际没用过的位置。
    """
    if slide_edges:
        return int(max(44, min(96, round(median(slide_edges)))))
    edges = []
    for layout in layouts:
        if layout.get("kind") not in ("bullets", "two_col", "other"):
            continue
        for ph in layout.get("placeholders") or []:
            if ph.get("ph_type") in ("TITLE", "CENTER_TITLE", "SLIDE_NUMBER", "FOOTER", "DATE"):
                continue
            left = float(ph.get("left") or 0) * tk.PX_PER_INCH
            width = float(ph.get("width") or 0) * tk.PX_PER_INCH
            if 8 <= left <= canvas_w * 0.2 and width >= canvas_w * 0.4:
                edges.append(left)
    if not edges:
        return 64
    return int(max(44, min(96, round(median(edges)))))


def _ratio_label(width_in: float, height_in: float) -> str:
    if width_in <= 0 or height_in <= 0:
        return "16:9"
    ratio = width_in / height_in
    for label, value in (("16:9", 16 / 9), ("4:3", 4 / 3), ("16:10", 1.6), ("3:2", 1.5), ("1:1", 1.0), ("9:16", 9 / 16)):
        if abs(ratio - value) < 0.02:
            return label
    return f"{ratio:.2f}:1"


# ---------------------------------------------------------------- 入口

def extract(
    prs: Presentation,
    *,
    palette: dict[str, str],
    fonts: dict[str, str],
    layouts: list[dict],
    slide_size: dict,
    name: str = "公司模板",
    pack_id: str = "",
) -> dict:
    """→ {"preset": 可直接喂 resolve_tokens 的 preset, "report": 抽取报告}。

    不抛异常：任何一路采样失败都只是少一份证据，最终仍会给出完整 preset。
    """
    samples = _Samples()
    # 尺寸缺失时按 16:9 兜底：slide_w=0 会让所有形状的面积比都算成 1.0，全被当底色
    slide_w = int(prs.slide_width or 0) or int(13.333 * _EMU_IN)
    slide_h = int(prs.slide_height or 0) or int(7.5 * _EMU_IN)
    notes: list[str] = []

    for master in prs.slide_masters:
        _sample_surface(master._element, master.part, palette, samples, 1.0, "master")
        _sample_shapes(master.shapes, master.part, palette, samples, 1.4, "master", slide_w, slide_h)
        for layout in master.slide_layouts:
            kind = _kind(layout.name or "")
            cover = kind == "cover"
            weight = 2.6 if cover else (2.0 if kind in ("bullets", "two_col") else 0.6)
            role = "cover" if cover else "content"
            _sample_surface(layout._element, layout.part, palette, samples, weight, role)
            _sample_shapes(layout.shapes, layout.part, palette, samples, weight * 0.4, role, slide_w, slide_h)

    for index, slide in enumerate(prs.slides):
        if index >= _MAX_SLIDES:
            break
        role = "cover" if index == 0 else "content"
        weight = 3.0 if index == 0 else 1.5
        _sample_surface(slide._element, slide.part, palette, samples, weight, role)
        _sample_shapes(slide.shapes, slide.part, palette, samples, weight * 0.8, role,
                       slide_w, slide_h, source="slide")

    mode, bg, cover_bg = _decide_mode(samples, palette)
    primary, secondary, ranked = _pick_accents(samples, palette, bg)
    if not secondary:
        secondary = color.rotate(primary, 152)
        notes.append("模板只解析出一支品牌色，副色按色相环派生")
    chart = _chart_ramp(primary, secondary, ranked, mode)

    theme_raw = fonts.get("minor_ea") or fonts.get("major_ea") or ""
    theme_body, theme_note = _resolve_font(theme_raw, cjk=True, fallback=tk.FONT_SANS)
    body_raw, display_raw = _body_font(samples, fonts), _display_font(samples, fonts)
    # 缺失字体优先回落模板自己声明的主题字族，而不是全局默认——保住模板的衬线/无衬线气质
    body, body_note = _resolve_font(body_raw, cjk=True, fallback=theme_body)
    display, display_note = _resolve_font(display_raw, cjk=False, fallback=body)
    if display in _CJK_FONTS and display == body:
        # 标题与正文同族时给标题一支几何感更强的拉丁字体，中文自动回落正文族
        display = tk.FONT_LATIN if _font_installed(tk.FONT_LATIN) else body
    seen_fonts: set[str] = set()
    for raw, note in ((body_raw, body_note), (display_raw, display_note), (theme_raw, theme_note)):
        key = (raw or "").strip()
        # 同一支缺失字体可能既是正文又是标题，说明只说一遍
        if note and key not in seen_fonts:
            seen_fonts.add(key)
            notes.append(note)

    if mode == "dark" and color.luminance(cover_bg) >= _DARK_BELOW:
        notes.append("模板封面为浅底、内页为深底，成稿统一按内页深色基调重绘")
    elif mode == "light" and color.luminance(cover_bg) < _DARK_BELOW:
        notes.append("模板封面为深底、内页为浅底，成稿统一按内页浅色基调重绘")

    colors = _palette(mode, bg, primary, secondary)
    width_in = float(slide_size.get("width_in") or 0)
    height_in = float(slide_size.get("height_in") or 0)
    canvas_w, canvas_h = tk.canvas_for(width_in, height_in)
    ratio = _ratio_label(width_in, height_in)
    out_w, out_h = canvas_w / tk.PX_PER_INCH, canvas_h / tk.PX_PER_INCH
    if width_in > 0 and height_in > 0 and (abs(out_w - width_in) > 0.02 or abs(out_h - height_in) > 0.02):
        notes.append(
            f"模板页面 {width_in:g}×{height_in:g}in 归一为 {out_w:g}×{out_h:g}in（保持 {ratio} 比例，物理尺寸有变化）"
        )

    margin = _content_margin(layouts, canvas_w, samples.slide_text_lefts)
    radius = int(max(0, min(28, round(median(samples.round_radii))))) if samples.round_radii else 14
    motif = _motif(samples, mode, display)
    stops = _cover_stops(samples, bg, colors["paper"], primary, mode)
    tagline = f"{'深色' if mode == 'dark' else '浅色'}底 · {ratio} · 取自上传模板"

    preset = {
        "key": f"template:{pack_id}" if pack_id else "template",
        "name": name or "公司模板",
        "tagline": tagline,
        "font": body,
        "palette": colors,
        "canvas": [canvas_w, canvas_h],
        "margin_px": margin,
        "family": {
            "key": f"template:{pack_id}" if pack_id else "template",
            "mode": mode,
            "motif": motif,
            "display_font": display,
            "body_font": body,
            "cover_stops": stops,
            "chart": chart,
            "grain": 0.05 if mode == "dark" else 0.08,
            "tagline": tagline,
        },
    }
    report = {
        "version": STYLE_VERSION,
        "mode": mode,
        "background": bg,
        "accent": primary,
        "accent2": secondary,
        "chart": chart,
        "cover_stops": stops,
        "motif": motif,
        "fonts": {
            "display": display, "body": body,
            "source_display": display_raw,
            "source_body": body_raw,
        },
        "canvas": [canvas_w, canvas_h],
        "ratio": ratio,
        "slide_size": {"width_in": round(width_in, 3), "height_in": round(height_in, 3)},
        "margin_px": margin,
        "radius_px": radius,
        "notes": notes,
        "evidence": {
            "backgrounds": len(samples.backgrounds),
            "fills": len(samples.fills),
            "cards": samples.card_shapes,
            "layouts": len(layouts),
        },
        "summary_zh": (
            f"{'深色' if mode == 'dark' else '浅色'}基调 · 主色 #{primary} · {body} · {ratio}"
            + (f" · {len(notes)} 处适配" if notes else "")
        ),
    }
    return {"preset": preset, "report": report}


def fallback(palette: dict[str, str], fonts: dict[str, str], slide_size: dict, name: str = "公司模板") -> dict:
    """抽取彻底失败时的兜底：只用主题色板拼一份 preset，并明确留痕。"""
    base = designs.default_preset()
    primary = palette.get("accent1") or base["palette"]["accent"]
    lt1 = palette.get("lt1") or "FFFFFF"
    mode = "dark" if color.luminance(lt1) < _DARK_BELOW else "light"
    bg = lt1 if mode == "light" else (palette.get("dk1") or base["palette"]["bg"])
    secondary = palette.get("accent2") or color.rotate(primary, 152)
    colors = _palette(mode, bg, primary, secondary)
    width_in = float(slide_size.get("width_in") or 0)
    height_in = float(slide_size.get("height_in") or 0)
    canvas = tk.canvas_for(width_in, height_in)
    body = tk.FONT_SANS
    chart = _chart_ramp(primary, secondary, [], mode)
    preset = {
        "key": "template",
        "name": name or "公司模板",
        "tagline": "取自上传模板主题",
        "font": body,
        "palette": colors,
        "canvas": list(canvas),
        "margin_px": 64,
        "family": {
            "key": "template", "mode": mode, "motif": "rule" if mode == "dark" else "grid",
            "display_font": tk.FONT_LATIN, "body_font": body,
            "cover_stops": _cover_stops(_Samples(), bg, colors["paper"], primary, mode),
            "chart": chart, "grain": 0.05 if mode == "dark" else 0.08,
            "tagline": "取自上传模板主题",
        },
    }
    report = {
        "version": STYLE_VERSION, "mode": mode, "background": bg,
        "accent": primary, "accent2": secondary, "chart": chart,
        "cover_stops": preset["family"]["cover_stops"], "motif": preset["family"]["motif"],
        "fonts": {"display": tk.FONT_LATIN, "body": body,
                  "source_display": fonts.get("major_latin", ""), "source_body": fonts.get("minor_ea", "")},
        "canvas": list(canvas), "ratio": _ratio_label(width_in, height_in),
        "slide_size": {"width_in": round(width_in, 3), "height_in": round(height_in, 3)},
        "margin_px": 64, "radius_px": 14,
        "notes": ["未能采样模板版面，配色仅取自主题定义，版式按内置风格重绘"],
        "evidence": {"backgrounds": 0, "fills": 0, "cards": 0, "layouts": 0},
        "summary_zh": "仅读到主题定义，版式按内置风格重绘",
    }
    return {"preset": preset, "report": report}


def _kind(layout_name: str) -> str:
    low = (layout_name or "").strip().lower()
    if "封面" in low or "title slide" in low or "标题幻灯片" in low:
        return "cover"
    if "两栏" in low or "two content" in low or "comparison" in low or "比较" in low:
        return "two_col"
    if "内容" in low or "content" in low:
        return "bullets"
    if "节" in low or "section" in low:
        return "section"
    return "other"