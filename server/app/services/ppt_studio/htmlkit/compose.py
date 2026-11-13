"""确认卡 → 每页 HTML。确定性版式编排，既是预览路径也是渲染兜底路径。

为什么不让模型自由写 HTML：模型写的 CSS 一旦溢出/重叠，返工成本比手写坐标还高。
这里把「设计」拆成两半——
- 内容侧（模型负责）：要点写什么、哪条是主角、数字是多少（deepen 层已经做了）；
- 构图侧（本模块负责）：版式选择、非均分栅格、母题、色彩节奏、图形化。
构图规则全部确定性，因此可单测、可复现、可在预览里实时重算。

版式清单与 outline.PAGE_LAYOUTS 对齐；auto 页复用 design_renderer 的规则引擎，
保证「换渲染器不换编排口径」。
"""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass, field

from app.services.ppt_studio import design_renderer as dr
from app.services.ppt_studio.htmlkit import color, icons, stylesheet, tokens as tk
from app.services.ppt_studio.outline import Outline, OutlinePage

# 一份文稿里图形页上限：再多就视觉疲劳（与 design_renderer 同口径）
MAX_GRAPHIC_PAGES = 3
# 散文正文收字号的下限：18px ≈ 13.5pt，仍在 lint 的投影舒适线（16px）之上
_PROSE_FLOOR = 18
# 正文里数字加粗的落点：数字 + 单位整体 nowrap，避免「0.4 / 秒」这种断行
_NUM = re.compile(
    r"\d[\d,，.]*\s*(?:%|‰|个百分点|万亿|亿|万|千|倍|个月|年|天|日|小时|分钟|秒"
    r"|人|台|家|次|个|元|美元|米|公里|条|套|件|张|页|位|名|轮|批|步|款|km|kg|t)?"
)


@dataclass
class SlideHtml:
    """一页的渲染输入。kind 用于 pptx 侧的版式统计，html 是完整文档。"""

    index: int
    kind: str
    title: str
    html: str
    chapter: str = ""
    notes: str = ""
    diagnostics: list[str] = field(default_factory=list)


def esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def highlight(text: str) -> str:
    """正文里的数字 → 强调色 run，给视线一个落点（不改文字本身）。"""
    out: list[str] = []
    last = 0
    for m in _NUM.finditer(text or ""):
        out.append(esc(text[last:m.start()]))
        out.append(f'<span class="num-hl">{esc(m.group(0))}</span>')
        last = m.end()
    out.append(esc((text or "")[last:]))
    return "".join(out)


def clean_bullets(page: OutlinePage, limit: int = 6) -> list[str]:
    items = [re.sub(r"\s+", " ", b).strip() for b in page.bullets if b and b.strip()]
    return items[:limit]


# ---------------------------------------------------------------- 构图计划


def plan_deck(outline: Outline, tokens: dict) -> tk.DeckPlan:
    """整份一次性编排：版式 + 变体 + 强调色。

    变体由 seed 决定（设计台「随机一版」只动 seed），因此同一份内容可以
    换构图而不换文案。数据色带**每页都从 chart[0] 起**：同一份文稿里
    「第一个/领先项」永远是品牌主色，读者不用每页重新认色；页面之间的
    变化交给版式变体和母题，不靠给数据换色制造花样。
    """
    pages = outline.flatten_pages()
    total = len(pages)
    plan = tk.DeckPlan()
    history: list[str] = []
    graphic_used = 0
    seed = int(tokens.get("seed") or 0)
    for index, page in enumerate(pages):
        layout = page.layout if page.layout and page.layout != "auto" else ""
        if not layout:
            layout = dr.decide_auto_layout(
                page, index=index, total=total, history=history, chart_used=graphic_used
            )
        # 用户显式选的版式若素材不够，降级到能成立的版式（不静默出空页）
        layout = _feasible(layout, page, index=index, total=total)
        # 要点里藏着一整条时间序列时优先成图：这比大数字更能说明趋势
        if layout != "chart" and graphic_used < MAX_GRAPHIC_PAGES and _inline_series(page.bullets):
            layout = "chart"
        # 同一串指标页不要三连「三张大数字卡」：首张留总览，之后换成达成轨道。
        # 单指标的深挖页（标题里就写着 62%→91%）即使自动规则给了要点流，也值得上轨道。
        if layout in ("stat", "bullets", "cards"):
            spans = _spans_of([b for b in page.bullets if b])
            recent = history[-2:]
            first_of_run = not any(item in ("stat", "metric") for item in recent)
            deep_dive = bool(spans) and _title_backs(page.title, spans[0])
            # 3 项以上走仪表盘，2 项走前后双条，单项走聚焦页——都别再退回大数字卡。
            # 大数字卡会把「62%→91%，提升 29 个百分点」的 29 当成指标，正好读错。
            if spans and (len(spans) >= 2 or deep_dive):
                layout = "metric"
        # 只有真正的数据图表计入「图形疲劳」额度；大数字页和时间轴不算
        if layout == "chart":
            graphic_used += 1
        if graphic_used > MAX_GRAPHIC_PAGES and layout == "chart":
            layout = "cards" if any(dr._card_split(b)[0] for b in page.bullets) else "bullets"
        plan.layouts.append(layout)
        plan.variants.append((index + seed) % 2)
        plan.accents.append(0)
        history.append(layout)
    return plan


def _feasible(layout: str, page: OutlinePage, *, index: int, total: int) -> str:
    """版式素材体检：图表没数据回落卡片、表格没列回落要点、空页回落要点。"""
    bullets = clean_bullets(page, limit=8)
    if index == 0 and layout not in ("cover",):
        return "cover"
    if index == total - 1 and layout not in ("end", "quote", "stat", "cards", "bullets", "chart", "timeline", "table"):
        return "end"
    if not bullets:
        # 封面/章节/结尾/附录本来就不靠要点撑版面，回落成空要点页只会得到一页空白
        if layout in ("section", "end", "appendix"):
            return layout
        return "cover" if index == 0 else "bullets"
    if layout == "chart" and not dr.extract_chart_series(bullets):
        return "cards" if sum(1 for b in bullets if dr._card_split(b)[0]) >= 3 else "bullets"
    if layout == "table" and not _table_rows(bullets):
        return "bullets"
    if layout == "timeline" and len(bullets) < 2:
        return "bullets"
    if layout == "stat" and len(bullets) < 2:
        return "bullets"
    return layout


# ---------------------------------------------------------------- 文档外壳


def render_document(body: str, tokens: dict, *, kind: str, extra_css: str = "") -> str:
    """页面外壳：注入变量 + 锁定样式表 + 一页 slide 根节点。"""
    root = stylesheet.root_style(tokens["css_vars"], tk.cover_gradient(tokens))
    return (
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        f"<style>{root}{stylesheet.stylesheet()}{extra_css}</style></head>"
        f'<body><div class="slide" data-kind="{esc(kind)}">{body}</div></body></html>'
    )


# ---------------------------------------------------------------- 母题装饰


def _motif(tokens: dict, kind: str, variant: int) -> str:
    """母题：出血大圆 / 细线网格 / 角标。全部用真实 div，能映射成 pptx 形状。"""
    motif = tokens.get("motif") or "none"
    if motif == "none":
        return ""
    parts: list[str] = []
    big = kind in ("cover", "section", "end")
    if motif == "glass" or (big and motif in ("rule", "index")):
        # 母题尺寸按画布宽缩放，否则 4:3 上这颗大圆会吃掉半页
        size = (520 if big else 300) * tokens["canvas_w"] / tk.CANVAS_W
        left = tokens["canvas_w"] - size * 0.42 if variant == 0 else -size * 0.34
        top = -size * 0.30 if variant == 0 else tokens["canvas_h"] - size * 0.55
        parts.append(
            f'<div class="m-orb" style="width:{size:.0f}px;height:{size:.0f}px;'
            f'left:{left:.0f}px;top:{top:.0f}px;opacity:{0.5 if big else 0.28:.2f}"></div>'
        )
    if motif == "grid":
        for i in range(1, 6):
            x = round(tokens["canvas_w"] * i / 6)
            parts.append(f'<div class="m-gridline" style="left:{x}px;top:0;width:1px;height:{tokens["canvas_h"]}px"></div>')
        for i in range(1, 4):
            y = round(tokens["canvas_h"] * i / 4)
            parts.append(f'<div class="m-gridline" style="left:0;top:{y}px;width:{tokens["canvas_w"]}px;height:1px"></div>')
    if motif == "index" and not big:
        parts.append(f'<div class="m-corner" style="left:0;top:0;width:64px;height:3px"></div>')
    if big:
        # 落在下边距的中线上：贴着版心下沿时（封面数字条正好收在那儿）只差 15px，
        # 看着像第一张卡漏出来的一条下划线，而不是页面装饰
        bleed = max(14, (tokens["margin"] - 18) // 2)
        parts.append(
            f'<div class="m-bar" style="left:{tokens["margin"]}px;bottom:{bleed}px;'
            'width:88px;height:3px"></div>'
        )
    return "".join(parts)


def _title_px(text: str, tokens: dict, size: str = "h1") -> int:
    """页头标题按版心宽收字号，只为躲开孤字行。

    4:3 比 16:9 窄四分之一，同一支 42px 标题常把最后一两个字甩到第二行
    （「…联网、换线、良」/「率」），看着像排版事故，还白吃掉 60px 正文高度。
    一行装得下、且代价不超过两成字号就收；收不动就保持原字号照常折行——
    页头不是封面，不该为了凑单行缩成小标题。
    """
    base = tokens["types"][size]
    width = tokens["canvas_w"] - 2 * tokens["margin"]
    fit = int(width * 0.94 / max(_text_em(text), 0.1))
    if fit >= base:
        return base
    return fit if fit >= int(base * 0.78) else base


def _head(page: OutlinePage, chapter: str, tokens: dict, *, size: str = "h1") -> str:
    """页头。标题用真标题标签：检测器里的孤字、折行失衡、层级跳跃都按标签找，
    全是 div 时这些规则等于没开。类名不变，实测与 pptx 映射都不受影响。"""
    kicker = (page.kicker or chapter or "").strip()
    title = page.title or "未命名页"
    tag = size if size in ("h1", "h2", "h3") else "h1"
    parts = ['<div class="head">']
    if kicker:
        parts.append(f'<div class="kicker">{esc(kicker[:40])}</div>')
    parts.append(
        f'<{tag} class="{size}" style="font-size:{_title_px(title, tokens, size)}px">{esc(title)}</{tag}>'
    )
    parts.append('<div class="rule"></div>')
    parts.append("</div>")
    return "".join(parts)


def _footer(page: OutlinePage, outline: Outline, index: int, total: int, tokens: dict) -> str:
    if not tokens.get("footer"):
        return ""
    labels = [s.title or s.url for s in page.sources if (s.title or s.url)]
    src = "来源：" + "、".join(labels[:2]) if labels else ""
    if len(src) > 62:
        src = src[:61] + "…"
    return (
        '<div class="footer">'
        f'<div class="foot-no">{index + 1:02d} / {total:02d}</div>'
        f'<div class="foot-src">{esc(src)}</div>'
        "</div>"
    )


# ---------------------------------------------------------------- 各版式


def _display_px(text: str, tokens: dict, width: int) -> int:
    """大标题按可用宽度收字号。

    字阶是物理磅值（4:3 与 16:9 的 1px 都是 1/96in），不能整体缩放；但 4:3 画布
    窄 25%，同一支 84px 标题会挤出一个字的孤行（「…经营复」/「盘」）。
    宁可整行小一点，也不要孤字。
    """
    base = tokens["types"]["cover"]
    em = sum(1.0 if ord(ch) > 0x2E80 else 0.58 for ch in text)
    return max(int(base * 0.6), min(base, int(width * 0.96 / max(em, 0.1))))


def _cover_text_vars(tokens: dict) -> str:
    """封面/结尾页的次要文字色，按渐变里最难读的那一档算。

    封面底色是渐变，`--c-muted` 是按白底挑的；落到最深的那个色标上只有 3.3:1，
    投影时基本看不清。这里取渐变两端里对正文最不利的一档，反推一版可读色，
    再用自定义属性局部覆盖——只影响封面与结尾页。
    """
    stops = tokens.get("cover_stops") or []
    if not stops:
        return ""
    ink = tokens["colors"]["ink"]
    worst = min(stops, key=lambda stop: color.contrast(stop, ink))
    muted = _readable_hue(worst, tokens["colors"]["muted"], tokens)
    accent = _readable_hue(worst, tokens["colors"]["accent_ink"], tokens)
    return f"--cover-muted:{muted};--cover-accent-ink:{accent}"


def _cover_sub(text: str) -> str:
    """封面副题按冒号断行。

    中文没有词边界，交给浏览器自然折行会从词中间劈开（「…良率、成 / 本、交付」），
    balance 也只是把两行裁得一样长，劈开的位置照样随机。冒号两边本来就是两层
    意思，在它后面强制换行，断点落在语义上而不是字符上。
    """
    text = text or ""
    if len(text) < 18:
        return esc(text)
    for mark in ("：", ":"):
        head, sep, tail = text.partition(mark)
        if sep and tail.strip():
            return f"{esc(head)}{sep}<br>{esc(tail)}"
    return esc(text)


def _cover(page: OutlinePage, outline: Outline, tokens: dict, variant: int) -> str:
    title = outline.title or page.title or "演示文稿"
    sub = outline.subtitle or page.title or ""
    kicker = (page.kicker or outline.audience or "PRESENTATION").strip()
    stats = _deck_stats(outline)
    stat_html = "".join(
        f'<div class="stat" style="background:{color.with_alpha(tokens["colors"]["card"], 0.55)}">'
        f'<div class="stat-num" style="font-size:{tokens["types"]["stat_sm"]}px">{esc(num)}'
        f'<span class="stat-unit">{esc(unit)}</span></div>'
        f'<div class="stat-label">{esc(label)}</div></div>'
        for num, unit, label in stats
    )
    meta = (
        f'<div class="cover-meta"><div class="small">{esc(outline.audience)}</div>'
        f'<div class="small">{outline.content_page_count()} 页 · {len(outline.sections)} 章</div></div>'
        if outline.audience
        else f'<div class="cover-meta"><div class="small">{outline.content_page_count()} 页 · {len(outline.sections)} 章</div></div>'
    )
    if variant == 0:
        title_px = _display_px(title, tokens, tokens["canvas_w"] - tokens["margin"] * 2)
        body = (
            '<div class="cover-wrap" style="' + _cover_text_vars(tokens) + '">'
            f'<div class="kicker">{esc(kicker[:44])}</div>'
            '<div class="grow"></div>'
            f'<h1 class="cover-title" style="font-size:{title_px}px">{esc(title)}</h1>'
            + (f'<div class="cover-sub" style="margin-top:18px">{_cover_sub(sub)}</div>' if sub and sub != title else "")
            + '<div class="rule" style="margin:26px 0 22px"></div>'
            + (f'<div class="cover-stats">{stat_html}</div>' if stat_html else meta)
            + "</div>"
        )
    else:
        narrow = int(tokens["canvas_w"] * 0.66)
        title_px = _display_px(title, tokens, narrow)
        body = (
            '<div class="cover-wrap" style="' + _cover_text_vars(tokens) + '">'
            f'<div class="kicker">{esc(kicker[:44])}</div>'
            f'<h1 class="cover-title" style="margin-top:30px;max-width:{narrow}px;font-size:{title_px}px">{esc(title)}</h1>'
            + (f'<div class="cover-sub" style="margin-top:20px;max-width:{int(tokens["canvas_w"] * 0.56)}px">{_cover_sub(sub)}</div>' if sub and sub != title else "")
            + '<div class="grow"></div>'
            '<div class="rule" style="margin-bottom:20px"></div>'
            + (f'<div class="cover-stats">{stat_html}</div>' if stat_html else meta)
            + "</div>"
        )
    return body


_LABEL_NUM = re.compile(
    r"^(?P<label>.{1,18}?)\s*[：:]?\s*"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|‰|个百分点|倍|(?:万亿|亿|万|千)?[台元人辆次家部个件套吨升度名只场届页字]?)"
    r"\s*[。；.]?$"
)


def _deck_stats(outline: Outline) -> list[tuple[str, str, str]]:
    """封面数字条：从全篇要点里挑真实数字，挑不到就不编（返回空）。"""
    found: list[tuple[str, str, str]] = []
    for page in outline.flatten_pages()[1:]:
        for bullet in clean_bullets(page, limit=8):
            m = _LABEL_NUM.match(bullet)
            if not m:
                continue
            found.append((
                m.group("value"),
                m.group("unit"),
                m.group("label").strip().rstrip("，,、的与和") or "关键指标",
            ))
            if len(found) >= 3:
                return found
    return found


def _section(page: OutlinePage, outline: Outline, tokens: dict, index: int, chapter_pages: list[str], chapter_no: int) -> str:
    items = "".join(
        f'<div class="sec-item"><div class="sec-no">{i + 1:02d}</div>'
        f'<div class="sec-name">{esc(t)}</div></div>'
        for i, t in enumerate(chapter_pages[:6])
    )
    left = (
        '<div class="sec-left">'
        f'<div class="kicker">{esc(page.kicker or "CHAPTER")}</div>'
        f'<div class="sec-index">{chapter_no:02d}</div>'
        f'<h1 class="sec-title">{esc(page.title or "章节")}</h1>'
        '<div class="rule rule--accent" style="width:88px"></div>'
        + (f'<div class="lead">{esc(page.bullets[0])}</div>' if page.bullets else "")
        + "</div>"
    )
    right = f'<div class="sec-list">{items}</div>' if items else '<div class="sec-list"></div>'
    return f'<div class="sec-wrap">{left}{right}</div>'


def _bullets(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int) -> str:
    bullets = clean_bullets(page, limit=6)
    hero = _hero_bullet(bullets)
    head = _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>'
    # 短列表 + 有数字 → 非均分双栏：左列表 + 右焦点卡（避免等宽均分构图）
    if hero and len(bullets) <= 4 and variant == 1:
        width = (tokens["canvas_w"] - 2 * tokens["margin"] - tokens["gap"]) * 0.67
        gutter = _prose_metrics(tokens, band=False)[2]
        fit = _prose_fit(bullets, width - gutter, _content_height(page, tokens), tokens)
        listing = (
            '<div class="col" style="flex:1 1 auto">'
            + _prose_stack(bullets, tokens, fit or (_PROSE_FLOOR, 9))
            + "</div>"
        )
        num, unit, label = hero
        focus = (
            '<div class="col" style="flex:0 0 33%">'
            '<div class="card card--accent grow" style="justify-content:center">'
            f'<div class="stat-num" style="font-size:{tokens["types"]["stat_sm"]}px">{esc(num)}'
            f'<span class="stat-unit">{esc(unit)}</span></div>'
            f'<div class="stat-label">{esc(label)}</div>'
            '<div class="rule"></div>'
            f'<div class="stat-note">本页焦点</div>'
            "</div></div>"
        )
        main = f'<div class="cols grow" style="align-items:stretch">{listing}{focus}</div>'
    elif len(bullets) <= 4:
        # 条目少：每条做成实底横幅，用版面重量填满版心，而不是把行距拉成空白
        main = _prose_main(bullets, page, tokens, prefer_band=True)
    else:
        main = _prose_main(bullets, page, tokens, prefer_band=False)
    return head + main


def _readable_hue(base: str, hue: str, tokens: dict) -> str:
    """把图表色相调成能在指定底色上读清的文字色（深底提亮、浅底压深）。"""
    towards = "FFFFFF" if tokens.get("mode") != "light" else "000000"
    return "#" + color.readable_on(
        base,
        (hue, color.blend(hue, towards, 0.22), color.blend(hue, towards, 0.42),
         color.blend(hue, towards, 0.62)),
    )


def _ink_hue(tokens: dict, index: int) -> str:
    """图表色落到卡面文字上时保证对比度。"""
    return _readable_hue(tokens["colors"]["card"], tk.chart_hue(tokens, index), tokens)


def _hero_bullet(bullets: list[str]) -> tuple[str, str, str] | None:
    """挑一条「标签 + 数字」当焦点；没有就不造焦点卡。"""
    for bullet in bullets:
        m = _LABEL_NUM.match(bullet)
        if m:
            return (
                m.group("value"),
                m.group("unit"),
                m.group("label").strip().rstrip("，,、的与和") or "关键指标",
            )
    return None


def _two_col(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int) -> str:
    """对照页：能拆出两个「小标题：正文」阵营就按阵营分栏，否则对半分。

    分栏后每栏是一张卡，标题在顶、条目均分剩余高度——不是两列浮空的要点。
    """
    bullets = clean_bullets(page, limit=6)
    parsed = [dr._card_split(b) for b in bullets]
    heads = [h for h, _ in parsed if h]
    groups: list[tuple[str, list[str]]] = []
    if len(heads) == len(parsed) and len(set(heads)) == 2:
        for head in dict.fromkeys(heads):
            groups.append((head, [body for h, body in parsed if h == head]))
    else:
        mid = math.ceil(len(bullets) / 2)
        groups = [("", bullets[:mid]), ("", bullets[mid:])]
    columns: list[str] = []
    height = _content_height(page, tokens) - 2 * tokens["pad"] - 4
    span = _span_width([1.0, 1.0], tokens, 0) - 2 * tokens["pad"] - 2
    gutter = _prose_metrics(tokens, band=False)[2]
    fits: list[tuple[int, int] | None] = []
    for ci, (head, rows) in enumerate(groups):
        # 栏里有小标题时，标题和它与要点流之间的 12px 间隙也要从高度预算里扣掉
        extra = tokens["types"]["h3"] * 1.43 + 12 if head else 0.0
        fits.append(_prose_fit(rows, span - gutter, height, tokens, extra=extra))
    if any(f is None for f in fits):
        # 两栏都装不下就并成整幅列表：与其把正文缩到看不清，不如放弃分栏。
        # 内容一条不删——这是换构图，不是减密度。
        return (
            _head(page, chapter, tokens)
            + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>'
            + _prose_main(bullets, page, tokens, prefer_band=False)
        )
    # 两栏共用一套字阶与内边距，取最紧的那栏：字号取小、内边距也取小才两栏都装得下
    chosen = min((f for f in fits if f), key=lambda f: (f[0], f[1]))
    for ci, (head, rows) in enumerate(groups):
        hue = tk.chart_hue(tokens, ci)
        stack = _prose_stack(rows, tokens, chosen, hue=ci)
        title = f'<h2 class="h3" style="--hue:{_ink_hue(tokens, ci)}">{esc(head)}</h2>' if head else ""
        columns.append(
            f'<div class="card card--topline" style="flex:1 1 0;--topline:#{hue}">'
            f'{title}{stack}</div>'
        )
    main = f'<div class="cols grow">{"".join(columns)}</div>'
    return _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>' + main


def _cards(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int, accent_index: int) -> str:
    bullets = clean_bullets(page, limit=6)
    parsed = [dr._card_split(b) for b in bullets]
    count = len(parsed)
    # 非均分：首卡更宽（1.35fr），其余等分——避免「等宽均分构图」
    weights = [1.35 if i == 0 else 1.0 for i in range(count)]
    if count >= 5:
        weights = [1.0] * count
    half = math.ceil(count / 2)
    head_html = _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>'
    # 单行优先（构图更整），装不下再拆两行；宽度按真实 flex 权重反推，
    # 首卡 1.35fr 比同排其余卡宽三成，按等分估会少算它的正文行数。
    splits = [([weights], [parsed])] if count <= 4 else []
    if count >= 3:
        splits.append(([weights[:half], weights[half:]], [parsed[:half], parsed[half:]]))
    budget = _content_height(page, tokens)
    chosen: tuple[list[list[float]], list[list[tuple[str, str]]], int] | None = None
    for row_weights, rows in splits:
        widths = [_span_width(rw, tokens, i) for rw in row_weights for i in range(len(rw))]
        # 正文栏的行长下限：每行不足 8 个汉字就不成「段」了，窄栏里正文折成七八行，
        # 读起来是一条竖着的字带而不是一张卡。光看高度装得下不算装得下。
        measure_ok = min(
            (w - 2 * tokens["pad"] - 2 for w, (_, b) in zip(widths, parsed) if not _bare_metric(b)),
            default=float("inf"),
        )
        flat = [(i, h, b) for i, (h, b) in enumerate(parsed)]
        bounds = [0] + [len(r) for r in rows]
        for size in range(tokens["types"]["body"], _PROSE_FLOOR - 1, -1):
            if measure_ok < 8 * size:
                continue
            need = tokens["gap"] * (len(rows) - 1)
            for ri, row in enumerate(rows):
                start = sum(bounds[:ri + 1])
                need += max(
                    _card_height(h or f"要点 {i + 1}", b, widths[i], size, tokens)
                    for i, h, b in flat[start:start + len(row)]
                )
            if need <= budget - 8:
                chosen = (row_weights, rows, size)
                break
        if chosen:
            break
    if chosen is None:
        # 卡片 chrome（序号 + 小标题 + 内边距）就要 160px 上下，窄画布再拆行就只剩
        # 一条字带的位置，收字号也救不回来。改用实底横幅：同样一条一卡的读法，
        # 全部正文原样保留——换构图，不减内容。
        return head_html + _prose_main(bullets, page, tokens, prefer_band=True)
    row_weights, rows, body_size = chosen
    widths = [_span_width(rw, tokens, i) for rw in row_weights for i in range(len(rw))]
    cards: list[str] = []
    for i, (head, body) in enumerate(parsed):
        hue = tk.chart_hue(tokens, accent_index + i)
        title = head or f"要点 {i + 1}"
        metric = _bare_metric(body)
        # chip 是 16% 色相叠在卡片上，文字色要按它真正落到的底算，
        # 拿 card 去算会差出 0.5 个对比度单位。
        chip_bg = color.blend(hue, tokens["colors"]["card"], 0.84)
        chip_color = _readable_hue(chip_bg, hue, tokens)
        # 「2025 年平均换线：48 分钟」这种整条就是一个数字的要点：把数字提为卡片主体，
        # 否则同一个数字会在正文和底部各出现一次，中间还空出一大片。
        if metric:
            num, unit = metric
            inner = widths[i] - 2 * tokens["pad"] - 8
            size = _fit_number(num, unit, inner, 104)
            cards.append(
                f'<div class="card" style="flex:{weights[i]:.2f} 1 0;gap:18px">'
                f'<div class="chip" style="background:{color.with_alpha(hue, 0.16)};--hue:{chip_color}">{i + 1:02d}</div>'
                f'<h2 class="h3">{esc(title)}</h2>'
                f'<div class="stat-num" style="font-size:{size}px;--hue:{_readable_hue(tokens["colors"]["card"], hue, tokens)}">{esc(num)}'
                f'<span class="stat-unit">{esc(unit)}</span></div>'
                "</div>"
            )
            continue
        # 卡片底部不再猜「结果数字」：散文里最后一个数字常常是旧值或年份
        # （「19 分钟…避免回到 48 分钟」会挑中 48），把它放大成结论是在替用户编结论。
        # 散文卡就让正文自己撑版面，数字由 highlight() 在行内着色。
        cards.append(
            f'<div class="card" style="flex:{weights[i]:.2f} 1 0">'
            '<div class="card-top" style="flex:0 0 auto">'
            f'<div class="chip" style="background:{color.with_alpha(hue, 0.16)};--hue:{chip_color}">{i + 1:02d}</div>'
            f'<h2 class="h3">{esc(title)}</h2>'
            f'<div class="body" style="font-size:{body_size}px">{highlight(body)}</div>'
            "</div>"
            "</div>"
        )
    # 卡片行按内容定高、整组垂直居中：拉满版心会让每张卡各自居中自己的内容，
    # 序号与标题在行内高低不齐（正文行数不同），底部还空出一大片。
    if len(rows) == 1:
        main = (
            '<div class="grow" style="display:flex;flex-direction:column;justify-content:center">'
            f'<div class="cols" style="flex:0 1 auto">{"".join(cards)}</div>'
            "</div>"
        )
    else:
        main = (
            '<div class="grow" style="display:flex;flex-direction:column;'
            'justify-content:center;gap:var(--gap)">'
            f'<div class="cols" style="flex:0 1 auto">{"".join(cards[:half])}</div>'
            f'<div class="cols" style="flex:0 1 auto">{"".join(cards[half:])}</div>'
            "</div>"
        )
    return head_html + main


def _bare_metric(body: str) -> tuple[str, str] | None:
    """整条要点就是一个数字（「2025 年平均换线：48 分钟」）时返回它，否则 None。"""
    metric = _outcome_metric(body)
    return metric if metric and _is_bare_metric(body, metric) else None


def _card_height(head: str, body: str, width: float, size: int, tokens: dict) -> float:
    """卡片自然高度：序号 + 小标题 + 正文；整条就是一个数字时按大数字算。"""
    inner = max(width - 2 * tokens["pad"] - 2, 1)
    h3 = tokens["types"]["h3"]
    chip = tokens["types"]["kicker"] * 1.4 + 12
    title = max(1, math.ceil(_text_em(head) * h3 / inner)) * h3 * 1.43
    chrome = 2 * tokens["pad"] + 2 + chip + title
    metric = _bare_metric(body)
    if metric:
        return chrome + 36 + _fit_number(metric[0], metric[1], inner - 6, 104) * 1.15
    lines = max(1, math.ceil(_text_em(body) * size / inner))
    return chrome + 26 + lines * size * 1.62


def _span_width(weights: list[float], tokens: dict, index: int) -> float:
    """按 flex 权重反推第 index 个卡片的像素宽度（版心宽 - 间隙后按权重分配）。"""
    region = tokens["canvas_w"] - 2 * tokens["margin"] - tokens["gap"] * max(0, len(weights) - 1)
    total = sum(weights) or 1.0
    return region * weights[index] / total


def _fit_number(num: str, unit: str, avail: float, ceiling: float, floor: float = 34.0) -> float:
    """按可用宽度反推大数字字号——大数字要真的「大」，但绝不撑破卡片。

    Inter SemiBold 数字约 0.60em，逗号/小数点约 0.30em；单位按 0.42 倍字号，
    中日韩字符占 1em、拉丁字符约 0.55em。留 6% 余量给字距和渲染误差。
    """
    unit = unit or ""  # 调用方可能来自可选正则分组，None 会让下面的遍历直接崩
    digits = sum(1 for ch in num if ch.isdigit())
    cjk = sum(1 for ch in num if ord(ch) > 0x2E80)
    narrow = len(num) - digits - cjk
    unit_em = sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in unit) * 0.42
    em = digits * 0.60 + cjk * 1.0 + narrow * 0.30 + unit_em + 0.06
    return round(max(floor, min(ceiling, avail / max(em, 0.6))), 1)


def _outcome_metric(text: str) -> tuple[str, str] | None:
    """取一句话里最后一个「数字 + 单位」当结果指标（中文简报的结果通常在句尾）。

    年份不是指标：「剩余 9% 未联网设备决定 2027 年…」要挑 9%，不是 2027。
    """
    matches = list(re.finditer(
        r"(\d[\d,]*(?:\.\d+)?)\s*(%|‰|个百分点|倍|万亿|亿|万|千|小时|分钟|秒|个月|年|天|条|台|元|人|次|家|个)?",
        text or "",
    ))
    if not matches:
        return None
    real = [m for m in matches if not _is_year(m.group(1), m.group(2) or "")]
    if not real:
        return None
    m = real[-1]
    return m.group(1), m.group(2) or ""


def _is_year(value: str, unit: str) -> bool:
    return unit in ("", "年") and value.isdigit() and len(value) == 4 and 1900 <= int(value) <= 2100


def _is_bare_metric(body: str, metric: tuple[str, str]) -> bool:
    """正文去掉这个数字后基本不剩东西 → 这条要点整体就是一个指标。"""
    token = re.escape(metric[0]) + r"\s*" + re.escape(metric[1])
    rest = re.sub(token, "", body or "").strip(" ：:，,、。.（）()约共")
    return len(rest) <= 2


def _text_em(text: str) -> float:
    """粗估文本宽度（em）：中日韩占 1em，拉丁与数字约 0.55em。"""
    return sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in text)


def _content_height(page: OutlinePage, tokens: dict) -> float:
    """正文区可用高度 = 版心高 - 页头 - 页头后的间隙。

    页头按实测构成算：kicker 行高 1.5、.head 的两道 11px 间隙、标题行高 normal
    （显示字体约 1.43）、末尾 1px 分隔线。标题折两行时页头就吃掉 165px，
    4:3 上只剩 334px 给正文——不扣干净就会把溢出留到页脚上。
    """
    content_w = tokens["canvas_w"] - 2 * tokens["margin"]
    title = page.title or "未命名页"
    h1 = _title_px(title, tokens)
    title_lines = max(1, math.ceil(_text_em(title) * h1 / max(content_w, 1)))
    head_h = tokens["types"]["kicker"] * 1.5 + 11 + title_lines * h1 * 1.43 + 12
    return tokens["canvas_h"] - 2 * tokens["margin"] - head_h - tokens["gap"]


def _prose_metrics(tokens: dict, *, band: bool) -> tuple[int, float, float, float]:
    """要点流的 (基准字号, 行高, 序号占宽, 单条固定高)。

    裸列表与实底横幅是两套排版：横幅正文走 t-lead / 1.58、序号走 t-h1 且占 76px，
    裸列表走 t-body / 1.62、序号 t-h2 占 52px。估高必须按各自那套算，否则「本来就
    放得下」的判断会失真，把没坏的版式也改了。
    """
    if band:
        return tokens["types"]["lead"], 1.58, 76 + 26, tokens["types"]["h1"] * 1.15
    return tokens["types"]["body"], 1.62, 52 + 24, tokens["types"]["h2"] * 1.15


def _prose_fit(
    texts: list[str], width: float, height: float, tokens: dict,
    *, extra: float = 0.0, band: bool = False,
) -> tuple[int, int] | None:
    """给一组散文条目挑「字号 + 条目上下内边距」，装不下返回 None。

    第一档就是现在的 CSS 默认值，所以本来就放得下的版式一个像素都不会变；只有真
    放不下时才逐档收，且先保字号再收内边距。下限 18px ≈ 13.5pt，仍在投影舒适线
    （16px）之上——宁可换版式，也不把正文缩到看不清。
    实底横幅没有上下内边距（高度由 flex 撑），所以同一批文字它能排得更大。
    """
    base, leading, _, no_h = _prose_metrics(tokens, band=band)
    pads = (0,) if band else (16, 12, 9)
    spacing = 14 * max(0, len(texts) - 1) if band else 0
    border = 2 if band else 1
    for size in range(base, min(base, _PROSE_FLOOR) - 1, -1):
        rows = [
            max(max(1, math.ceil(_text_em(t) * size / max(width, 1))) * size * leading, no_h)
            for t in texts
        ]
        for pad in pads:
            # 末尾留 8px 余量：折行与行高都是估算，估少一点就 spill 到页脚上
            need = sum(rows) + len(texts) * (2 * pad + border) + spacing + extra
            if need <= height - 8:
                return size, pad
    return None


def _prose_stack(
    texts: list[str], tokens: dict, fit: tuple[int, int], *,
    band: bool = False, hue: int | None = None,
) -> str:
    """要点流 HTML。hue 给定时整摞用同一个图表色相（分栏时一栏一色）。"""
    size, pad = fit
    items: list[str] = []
    for i, text in enumerate(texts):
        index = i if band else hue
        tint = "" if index is None else f' style="--hue:{_ink_hue(tokens, index)}"'
        no = f'<div class="b-no"{tint}>{i + 1:02d}</div>'
        body = f'<div class="b-text" style="font-size:{size}px">{highlight(text)}</div>'
        if band:
            items.append(
                f'<div class="b-band" style="--rail:#{tk.chart_hue(tokens, i)}">{no}{body}</div>'
            )
        else:
            items.append(f'<div class="b-item" style="padding:{pad}px 0">{no}{body}</div>')
    kind = "bullets bullets--band" if band else "bullets"
    return f'<div class="{kind}">{"".join(items)}</div>'


def _prose_main(bullets: list[str], page: OutlinePage, tokens: dict, *, prefer_band: bool) -> str:
    """整幅要点流：横幅比裸列表省掉上下内边距，谁能用更大字号装下就用谁。"""
    height = _content_height(page, tokens)
    width = tokens["canvas_w"] - 2 * tokens["margin"]
    plain = _prose_fit(bullets, width - _prose_metrics(tokens, band=False)[2], height, tokens)
    boxed = _prose_fit(bullets, width - 62 - _prose_metrics(tokens, band=True)[2], height, tokens, band=True)
    if boxed and (not plain or boxed[0] > plain[0] or (prefer_band and boxed[0] >= plain[0])):
        return _prose_stack(bullets, tokens, boxed, band=True)
    return _prose_stack(bullets, tokens, plain or (_PROSE_FLOOR, 9))


def _stat_number_cap(page: OutlinePage, tokens: dict, rows: int, inner: float, note: str, note_px: int, base: int) -> int:
    """大数字的字号上限：宽度之外还要看高度预算。

    只按宽度反推时，4:3 拆成两行后每张卡只剩 ~200px 高，116px 的数字加上说明
    直接顶穿画布。这里把版心高度扣掉页头与行距摊到每行，再扣内边距、色条和说明
    实际要占的行数，剩下的才是数字能用的空间。
    """
    # 行与行之间只有 rows-1 道间隙（_content_height 已经扣掉页头后那一道）
    avail = _content_height(page, tokens) - tokens["gap"] * max(0, rows - 1)
    note_lines = max(1, math.ceil(_text_em(note) * note_px / max(inner, 1)))
    note_h = note_lines * note_px * 1.55
    # 末尾留 16px 余量：行高与折行都是估算，估少一点就 spill 到页脚上
    room = avail / max(rows, 1) - 2 * tokens["pad"] - 34 - note_h - 16
    return int(max(32, min(base, room / 1.15)))


def _stat(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int, accent_index: int) -> str:
    bullets = clean_bullets(page, limit=4)
    # 4:3 上四栏每栏只剩 ~150px，大数字挤成小标签、说明折成十几行捅穿画布。
    # 拆成两行两栏，栏宽翻倍，数字才撑得起「数据页」的版面重量。
    per_row = 2 if len(bullets) == 4 and tokens["canvas_w"] < tk.CANVAS_W else len(bullets)
    per_row = max(1, per_row)
    row_count = max(1, (len(bullets) + per_row - 1) // per_row)
    rows: list[str] = []
    for start in range(0, len(bullets), per_row):
        group = bullets[start:start + per_row]
        weights = [1.5 if i == 0 and len(group) > 2 else 1.0 for i in range(len(group))]
        cards: list[str] = []
        for offset, bullet in enumerate(group):
            i = start + offset
            num, unit, rest = _split_stat(bullet)
            hue = tk.chart_hue(tokens, accent_index + i)
            rule = f'<div class="rule rule--accent" style="width:64px;background:#{hue}"></div>'
            # 深化后的要点常是 30~45 字的整句，不是标签。按标签字号（t-h3）排，
            # 窄栏里一句能折成十几行；整句一律走注释字号，全文保留不截断。
            long_note = len(rest) > 18
            label_class = "stat-note" if long_note else "stat-label"
            note_px = tokens["types"]["small" if long_note else "h3"]
            inner = _span_width(weights, tokens, offset) - 2 * tokens["pad"] - 8
            cap = _stat_number_cap(
                page, tokens, row_count, inner, rest, note_px,
                150 if weights[offset] > 1 else 116,
            )
            if num:
                # 大数字按卡片宽高反推字号：数字本身就是版面主体，不能缩成小标签
                size = _fit_number(num, unit, inner, cap, floor=32)
                lead = (
                    f'<div class="stat-num" style="font-size:{size}px;--hue:{_readable_hue(tokens["colors"]["card"], hue, tokens)}">{esc(num)}'
                    f'<span class="stat-unit">{esc(unit)}</span></div>'
                )
            else:
                # 这条要点没有数字：用大号序号占住同样的版面重量，别把散文塞进数字槽。
                # 半透明色相在深底卡上只有 1.8:1，序号会糊成背景，得按卡面算可读色。
                # 序号默认走 t-index（132px），比数字还高，同样要收进高度预算。
                lead = (
                    f'<div class="card-ghost" style="font-size:{min(cap, tokens["types"]["index"])}px;'
                    f'--hue:{_readable_hue(tokens["colors"]["card"], hue, tokens)}">'
                    f"{i + 1:02d}</div>"
                )
            cards.append(
                '<div class="stat">'
                + lead
                + rule
                + f'<div class="{label_class}">{esc(rest)}</div>'
                + "</div>"
            )
        columns = " ".join(f"{w:.2f}fr" for w in weights)
        rows.append(f'<div class="stats" style="grid-template-columns:{columns}">{"".join(cards)}</div>')
    joined = "".join(rows)
    main = joined if len(rows) == 1 else (
        '<div class="grow" style="display:flex;flex-direction:column;'
        f'gap:var(--gap);min-height:0">{joined}</div>'
    )
    return _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>' + main


def _split_stat(text: str) -> tuple[str, str, str]:
    """把一条要点拆成「大数字 + 单位 + 说明」。拆不出数字就返回空数字。

    绝不能拿散文的前几个字充当数字：大数字槽是 100px+ 字号，六个汉字进去
    就会折成一根 1000px 高的柱子，直接捅穿画布。
    """
    text = (text or "").strip()
    m = re.match(
        r"^\s*(\d[\d,]*(?:\.\d+)?)\s*"
        r"(%|‰|个百分点|万亿|亿|万|千|倍|个月|小时|分钟|毫秒|秒|年|天|周|日"
        r"|条|套|件|张|页|位|名|轮|批|步|款|台|元|人|辆|次|家|部|个|吨|升|度|只|场|届|字)?\s*"
        r"[：:，,、]?\s*(.*)$",
        text,
    )
    if m:
        # 单位是可选的：正则没匹配到时 group(2) 是 None，后面按字符串用会崩
        return m.group(1), m.group(2) or "", m.group(3).strip() or "关键指标"
    labelled = _LABEL_NUM.match(text)
    if labelled:
        return (
            labelled.group("value"),
            labelled.group("unit") or "",
            labelled.group("label").strip().rstrip("，,、的与和") or "关键指标",
        )
    outcome = _outcome_metric(text)
    if outcome:
        return outcome[0], outcome[1], text
    return "", "", text



# ---------------------------------------------------------------- 指标聚焦
# 「A→B」的单向变化：62%→91%、48 分钟压到 19 分钟、93.4% 到 97.8%
_SPAN = re.compile(
    r"(?P<label>.{0,20}?)\s*[:：]?\s*"
    r"(?P<before>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<u1>%|‰|个百分点|分钟|小时|秒|天|倍|台|条|件|元|万元|亿|万)?"
    r"\s*(?:→|->|=>|[\u4e00-\u9fa5]{0,2}到|至)\s*"
    r"(?P<after>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<u2>%|‰|个百分点|分钟|小时|秒|天|倍|台|条|件|元|万元|亿|万)?"
)


@dataclass
class _Span:
    label: str
    before: float
    after: float
    unit: str
    text: str
    before_txt: str = ""
    after_txt: str = ""

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def change(self) -> float:
        return self.delta / abs(self.before) if self.before else 0.0

    def scale(self) -> float:
        """轨道的量程。百分比用 0~100，其它用两者中较大的那个。"""
        if self.unit in ("%", "‰") and max(self.before, self.after) <= 100:
            return 100.0
        return max(self.before, self.after, 1.0)


def _span_of(text: str) -> _Span | None:
    match = _SPAN.search(text or "")
    if not match:
        return None
    unit = (match.group("u2") or match.group("u1") or "").strip()
    try:
        before = float(match.group("before").replace(",", ""))
        after = float(match.group("after").replace(",", ""))
    except ValueError:
        return None
    return _Span(
        label=match.group("label").strip().rstrip("，,、的与和：:从由自于"),
        before=before, after=after, unit=unit, text=text,
        before_txt=match.group("before"), after_txt=match.group("after"),
    )


def _spans_of(bullets: list[str]) -> list[_Span]:
    return [span for span in (_span_of(b) for b in bullets) if span]


def _label_of(span: _Span) -> str:
    """指标名：截到第一个数字之前，再去掉「提升/降到」这类动词尾巴。

    「一次良率提升 4.4 个百分点，从 93.4% 到 97.8%」的标签应该是「一次良率」，
    而不是把前半句全抄进去。
    """
    head = re.split(r"\d", span.label or "")[0]
    head = head.strip().rstrip("，,、的与和：:从由自于")
    head = re.sub(r"(提升|提高|降低|下降|缩短|压缩|增长|回落|升到|降到|至|约为|约)+$", "", head)
    head = re.sub(r"(一年|全年|今年|本年|去年|这一年)+$", "", head)
    head = head.strip("，,、的与和 ")
    return head[:12] or "关键指标"


def _delta_text(span: _Span) -> str:
    """变化量：百分比指标说「个百分点」，其余给绝对值 + 相对幅度。"""
    sign = "+" if span.delta >= 0 else "−"
    amount = abs(span.delta)
    amount_txt = f"{amount:g}"
    if span.unit in ("%", "‰"):
        return f"{sign}{amount_txt} 个百分点"
    relative = f"（{sign}{abs(span.change) * 100:.0f}%）" if span.change else ""
    return f"{sign}{amount_txt} {span.unit}{relative}".strip()


def _title_backs(title: str, span: _Span) -> bool:
    """标题里同时出现起点和终点，说明这页就是围着这个指标写的。"""
    text = title or ""
    return span.before_txt in text and span.after_txt in text


def _metric_name(span: _Span, hue: str) -> str:
    """指标名 + 一个线性图标。

    只放在指标名前面，做成 20px 的小字形；不做「图标块 + 标题」那种套路——
    检测器的 icon-tile-stack 就是冲它去的，而且那是最典型的模板味。
    """
    label = _label_of(span)
    glyph = ""
    name = icons.name_for(label)
    if name:
        glyph = icons.svg(name, size=20, color=hue)
    return f'<div class="metric-name">{glyph}<div class="metric-label">{esc(label)}</div></div>'


def _metric_page(page: OutlinePage, outline: Outline, tokens: dict, chapter: str,
                 variant: int, accent_index: int) -> str:
    """把「A→B」画成一条达成轨道，而不是再摆三张等权大数字卡。

    一个主指标：左大数字 + 轨道 + 右解构；两到三个指标：多行前后对比条。
    数字一个都不编——没有「前→后」的页面不会走到这个版式。
    """
    bullets = clean_bullets(page, limit=6)
    spans = _spans_of(bullets)
    head = _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>'
    if not spans:
        return head + _prose_main(bullets, page, tokens, prefer_band=True)
    rest = [b for b in bullets if not any(b == span.text for span in spans)]
    if len(spans) >= 3:
        # 仪表盘：3~6 项指标一页看全，一行一条轨道，正文照旧留在下方注解里
        return head + _metric_rows(spans[:6], tokens, accent_index, compact=True) + _metric_foot(rest)
    if len(spans) == 2:
        return head + _metric_rows(spans[:2], tokens, accent_index) + _metric_foot(rest)

    span = spans[0]
    width = (tokens["canvas_w"] - 2 * tokens["margin"]) * 0.54
    rest = [b for b in bullets if b != span.text]
    fit = _prose_fit(rest, width - _prose_metrics(tokens, band=False)[2],
                     _content_height(page, tokens), tokens)
    notes = _prose_stack(rest, tokens, fit or (_PROSE_FLOOR, 9)) if rest else ""
    hue = _readable_hue(tokens["colors"]["card"], tk.chart_hue(tokens, accent_index), tokens)
    body = (
        '<div class="metric-spot grow">'
        f'<div class="metric-hero" style="--hue:{hue}">'
        f'{_metric_name(span, hue)}'
        f'<div class="metric-big">{esc(span.after_txt)}'
        f'<span class="metric-unit">{esc(span.unit)}</span></div>'
        f'<div class="metric-from">原 {esc(span.before_txt)}{esc(span.unit)} · {esc(_delta_text(span))}</div>'
        + _track(span, hue)
        + '</div>'
        f'<div class="metric-notes">{notes}</div>'
        '</div>'
    )
    return head + body


def _metric_rows(spans: list[_Span], tokens: dict, accent_index: int,
                 *, compact: bool = False) -> str:
    """多指标页画成前后双条：灰条是原来，色条是现在。

    比「一条轨道 + 一个标记」更直白——读者不用先理解标记是什么意思，
    两根条的长短自己会说话；也不给「越小越好」的指标造成误导。
    """
    rows: list[str] = []
    for index, span in enumerate(spans):
        # 仪表盘用同一个强调色：一页里四五行不同颜色，读者会以为颜色有含义。
        # 单指标聚焦页之间才换色相（那是跨页的节奏）。
        tone = accent_index if compact else accent_index + index
        hue = _readable_hue(tokens["colors"]["card"], tk.chart_hue(tokens, tone), tokens)
        scale = span.scale()
        before = max(1.0, min(100.0, span.before / scale * 100))
        after = max(1.0, min(100.0, span.after / scale * 100))
        ghost = color.blend(tk.chart_hue(tokens, tone), tokens["colors"]["ink"], 0.62)
        if compact:
            # 仪表盘一行：名称/现值 + 一条轨道 + 变化量。五项指标一页看全。
            rows.append(
                f'<div class="metric-row metric-row--line" style="--hue:{hue}">'
                f'<div class="metric-head">{_metric_name(span, hue)}'
                f'<div class="metric-value">{esc(span.after_txt)}'
                f'<span class="metric-unit">{esc(span.unit)}</span></div></div>'
                + _track(span, hue)
                + f'<div class="metric-delta">{esc(_delta_text(span))}</div>'
                '</div>'
            )
        else:
            rows.append(
                f'<div class="metric-row" style="--hue:{hue}">'
                f'<div class="metric-head">{_metric_name(span, hue)}'
                f'<div class="metric-value">{esc(span.after_txt)}'
                f'<span class="metric-unit">{esc(span.unit)}</span></div></div>'
                '<div class="metric-bars">'
                f'<div class="metric-bar"><div class="metric-bar-fill" style="width:{before:.1f}%;background:#{ghost}"></div></div>'
                f'<div class="metric-bar"><div class="metric-bar-fill" style="width:{after:.1f}%;background:{hue}"></div></div>'
                f'<div class="metric-bar-tags"><span>原 {esc(span.before_txt)}{esc(span.unit)}</span>'
                f'<span>现 {esc(span.after_txt)}{esc(span.unit)}</span></div>'
                '</div>'
                f'<div class="metric-delta">{esc(_delta_text(span))}</div>'
                '</div>'
            )
    kind = "metrics metrics--lines" if compact else "metrics"
    return f'<div class="{kind} grow">{"".join(rows)}</div>'


def _metric_foot(bullets: list[str]) -> str:
    """对比条下面那条注解：说明「为什么变了」，一个字都不丢。"""
    items = [b for b in bullets if b and b.strip()][:3]
    if not items:
        return ""
    body = "".join(f'<div class="item">{highlight(b)}</div>' for b in items)
    return f'<div class="metric-foot">{body}</div>'


def _track(span: _Span, hue: str) -> str:
    """一条轨道：底色是量程，实心是当前值，竖线是起点。"""
    scale = span.scale()
    fill = max(2.0, min(100.0, span.after / scale * 100))
    mark = max(0.0, min(100.0, span.before / scale * 100))
    return (
        '<div class="metric-track">'
        f'<div class="metric-fill" style="width:{fill:.1f}%"></div>'
        f'<div class="metric-mark" style="left:{mark:.1f}%"></div>'
        '</div>'
    )



# 内联序列：「1 月 88.2%、2 月 89.1%、3 月 90.6%」或「Q1 82 / Q2 86 / Q3 89」
_SERIES_POINT = re.compile(
    r"(?P<label>(?:\d{4}\s*年?|[Qq][1-4]|第?[一二三四五六七八九十]+季度|"
    r"[01]?\d\s*月|上半年|下半年|H[12]))\s*[:：]?\s*"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>%|‰|分钟|小时|秒|天|倍|台|条|件|元|万元|亿|万)?"
)
# 「1—4 月 88.2%、89.1%、90.6%、92.3%」：区间 + 一串数值，标签按序补
_SERIES_RANGE = re.compile(
    r"(?P<start>[01]?\d)\s*[—\-~至]\s*(?P<end>[01]?\d)\s*月[^\d%]{0,12}?"
    r"(?P<values>(?:\d[\d,]*(?:\.\d+)?\s*(?:%|‰)?\s*[、,，/]?\s*){3,})"
)


def _inline_series(bullets: list[str]) -> list[tuple[str, float, str]] | None:
    """从散文中抽出一条时间序列；抽不出就返回 None。

    和 `extract_chart_series` 的区别：那个要求每条要点本身就是「名称 数值」，
    而真实文稿常把一条序列写在一句话里（「1—4 月良率 88.2%、89.1%…」）。
    这里只捡原文里已经出现的数字，不推算、不插值。
    """
    text = " ".join(b for b in bullets if b)
    unit = ""
    pairs: list[tuple[str, float, str]] = []
    for match in _SERIES_POINT.finditer(text):
        value = _to_number(match.group("value"))
        if value is None:
            continue
        pairs.append((match.group("label").strip(), value, (match.group("unit") or "").strip()))

    if len(pairs) < 3:
        pairs = []
        span = _SERIES_RANGE.search(text)
        if span:
            start, end = int(span.group("start")), int(span.group("end"))
            values = re.findall(r"\d[\d,]*(?:\.\d+)?", span.group("values"))
            if 3 <= len(values) <= 12 and end >= start:
                labels = [f"{start + i} 月" for i in range(len(values))]
                pairs = [(label, _to_number(v), "") for label, v in zip(labels, values) if _to_number(v) is not None]
    if len(pairs) < 3:
        return None
    units = {unit for _, _, unit in pairs if unit}
    if len(units) > 1:
        return None
    unit = next(iter(units), "") if units else ""
    ordered = sorted(pairs, key=lambda item: dr._time_key(item[0]))
    return [(label, value, unit or "") for label, value, _ in ordered]


def _to_number(value: str) -> float | None:
    try:
        return float((value or "").replace(",", ""))
    except ValueError:
        return None


def _chart_kind(pairs: list[tuple[str, float, str]]) -> str:
    """图表类型由数据语义决定，不由单位决定。

    上一版渲染器「见到 % 就画饼图」，把「1 月 88.2% / 3 月 90.6%」这种
    月度合格率画成了环形占比，还算出「19%」这种没有意义的份额——这是
    内容层面的错误，比排版丑更严重。这里的判定顺序：
    1. 标签全是时间 → 柱状趋势，任何情况下都不饼图；
    2. 单位是 % 且合计 ≈ 100 → 环形构成图；
    3. 单位是 % 但合计不是 100（合格率、渗透率…）→ 柱状对比；
    4. 其余 → 横条排名。
    """
    if not pairs:
        return "bar"
    labels = [label for label, _, _ in pairs]
    is_time = all(dr._TIME_LABEL.search(label or "") for label in labels)
    units = {unit for _, _, unit in pairs}
    total = sum(abs(value) for _, value, _ in pairs)
    if is_time:
        return "column"
    if units == {"%"} and 95.0 <= total <= 105.0 and 3 <= len(pairs) <= 6:
        return "donut"
    if units == {"%"}:
        return "column"
    return "bar"


def _chart(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int, accent_index: int) -> str:
    bullets = clean_bullets(page, limit=8)
    pairs = dr.extract_chart_series(bullets) or _inline_series(bullets) or []
    kind = _chart_kind(pairs)
    unit = pairs[0][2] if pairs else ""
    is_time = bool(pairs) and all(dr._TIME_LABEL.search(p[0] or "") for p in pairs)
    if kind == "donut":
        graphic = _donut(pairs, tokens)
    elif kind == "column":
        # 窄区间（如 88→92 的合格率）柱状几乎等高，改用折线才看得出趋势；
        # 区间宽的（如 20→90）仍然用柱状，零基线才有对照意义。
        values = [value for _, value, _ in pairs]
        narrow = len(pairs) >= 4 and (max(values) - min(values)) <= 0.18 * max(max(values), 1)
        graphic = _spark(pairs, tokens, accent_index) if narrow else _columns(pairs, tokens, is_time=is_time)
    else:
        graphic = _bars(pairs, tokens)
    if is_time:
        lead, lead_word = pairs[-1], "最新"
    else:
        lead, lead_word = max(pairs, key=lambda p: abs(p[1])), ("占比最高" if kind == "donut" else "领先")
    # 零基线柱状图在「合格率 88→96」这种窄区间上几乎看不出变化，
    # 所以把首末差值直接写出来——既不截断坐标轴误导读者，又把趋势说清楚。
    delta_html = ""
    if is_time and len(pairs) >= 2:
        delta = pairs[-1][1] - pairs[0][1]
        suffix = "pp" if unit == "%" else unit
        delta_html = (
            f'<div class="chart-delta" style="--hue:{_ink_hue(tokens, 0)}">'
            f'较 {esc(pairs[0][0])} {"+" if delta >= 0 else "−"}{abs(delta):g}{esc(suffix)}</div>'
        )
    head = (
        '<div class="chart-head">'
        f'<h2 class="h3">{esc(lead[0])}</h2>'
        f'<div class="small">{lead_word} · {lead[1]:g}{esc(lead[2])}</div>'
        + delta_html
        + (f'<div class="chart-note" style="margin-left:auto">共 {len(pairs)} 组'
           + (f' · 单位 {esc(unit)}' if unit else "") + "</div>")
        + "</div>"
    )
    main = '<div class="chart-wrap">' + head + graphic + "</div>"
    return _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>' + main


def _spark(pairs: list[tuple[str, float, str]], tokens: dict, accent_index: int) -> str:
    """折线：窄区间趋势用线，柱子会看不出变化（88→92 的柱子几乎一样高）。

    视觉孪生用 SVG，只影响截图；pptx 侧由 spark 指令画成原生连接符，
    每个顶点都是可编辑形状。
    """
    values = [value for _, value, _ in pairs]
    low_raw, high_raw = min(values), max(values)
    span = max(high_raw - low_raw, 1e-6)
    low, high = low_raw - span * 0.25, high_raw + span * 0.25
    vw = vh = 1000.0
    n = len(pairs)
    points = [
        (round(index / max(n - 1, 1) * vw, 1), round((1 - (value - low) / (high - low)) * vh, 1))
        for index, (_, value, _) in enumerate(pairs)
    ]
    hue = tk.chart_hue(tokens, accent_index)
    polyline = " ".join(f"{x},{y}" for x, y in points)
    labels = "".join(f"<span>{esc(label)}</span>" for label, _, _ in pairs)
    values_html = "".join(f"<span>{value:g}</span>" for _, value, _ in pairs)
    payload = json.dumps([[x, y] for x, y in points])
    return (
        '<div class="spark-wrap grow">'
        f'<div class="spark-values">{values_html}</div>'
        f'<div class="spark" data-pptx="spark" data-points="{payload}" data-vw="1000" data-vh="1000" '
        f'data-color="{hue}" data-dots="0" data-radius="6" data-stroke="3">'
        f'<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" width="100%" height="100%">'
        f'<polyline points="{polyline}" fill="none" stroke="#{hue}" stroke-width="4" '
        'vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg></div>'
        f'<div class="spark-axis">{labels}</div>'
        '</div>'
    )


def _bars(pairs: list[tuple[str, float, str]], tokens: dict) -> str:
    top = max((abs(v) for _, v, _ in pairs), default=1) or 1
    rows: list[str] = []
    for i, (label, value, unit) in enumerate(pairs):
        hue = tk.chart_hue(tokens, i)
        pct = max(3.0, abs(value) / top * 100)
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-label">{esc(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{_fill(hue, tokens)}"></div></div>'
            f'<div class="bar-value">{value:g}{esc(unit)}</div>'
            "</div>"
        )
    return f'<div class="bars">{"".join(rows)}</div>'


def _columns(pairs: list[tuple[str, float, str]], tokens: dict, *, is_time: bool = False) -> str:
    top = max((abs(v) for _, v, _ in pairs), default=1) or 1
    items: list[str] = []
    for i, (label, value, unit) in enumerate(pairs):
        # 时序是「同一个指标的多个时点」→ 同色，末点提亮当落点；
        # 离散类别才用多色带，否则读者会以为每个柱子是不同指标。
        hue = tk.chart_hue(tokens, 0 if is_time else i)
        if is_time and i == len(pairs) - 1:
            hue = color.blend(hue, "FFFFFF", 0.3) if tokens.get("mode") != "light" else color.blend(hue, "000000", 0.2)
        pct = max(8.0, abs(value) / top * 74)
        items.append(
            '<div class="col-item">'
            f'<div class="col-value">{value:g}{esc(unit)}</div>'
            f'<div class="col-bar" style="height:{pct:.1f}%;background:{_fill(hue, tokens, vertical=True)}"></div>'
            f'<div class="col-label">{esc(label)}</div>'
            "</div>"
        )
    return (
        '<div style="display:flex;flex-direction:column;gap:12px;flex:1 1 auto;min-height:0">'
        f'<div class="columns">{"".join(items)}</div>'
        '<div class="col-base"></div>'
        "</div>"
    )


def _donut(pairs: list[tuple[str, float, str]], tokens: dict) -> str:
    """环形图：浏览器用 SVG 画，pptx 侧读 data-pptx 指令画原生 PIE + 中心洞。"""
    total = sum(abs(v) for _, v, _ in pairs) or 1
    size = 330
    cx = cy = size / 2
    radius = size / 2 - 4
    hole = 0.62
    segments: list[dict] = []
    paths: list[str] = []
    angle = -90.0
    for i, (_label, value, _unit) in enumerate(pairs):
        sweep = abs(value) / total * 360
        hue = tk.chart_hue(tokens, i)
        segments.append({"start": round(angle, 3), "end": round(angle + sweep, 3), "fill": hue})
        paths.append(f'<path d="{_arc(cx, cy, radius, angle, angle + sweep)}" fill="#{hue}"></path>')
        angle += sweep
    hole_fill = tokens["colors"]["bg"]
    paths.append(f'<circle cx="{cx}" cy="{cy}" r="{radius * hole:.1f}" fill="#{hole_fill}"></circle>')
    lead_label, lead_value, lead_unit = pairs[0]
    share = abs(lead_value) / total * 100
    svg = (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'data-pptx="donut" data-hole="{hole}" data-hole-fill="{hole_fill}" '
        f"data-segments='{esc(json.dumps(segments))}'>"
        + "".join(paths) + "</svg>"
    )
    top = max((abs(v) for _, v, _ in pairs), default=1) or 1
    legend = "".join(
        '<div class="lg-item">'
        f'<div class="lg-dot" style="background:#{tk.chart_hue(tokens, i)}"></div>'
        f'<div class="lg-label">{esc(label)}</div>'
        f'<div class="lg-track"><div class="lg-fill" style="width:{abs(value) / top * 100:.1f}%;'
        f'background:#{tk.chart_hue(tokens, i)}"></div></div>'
        f'<div class="lg-value">{value:g}{esc(unit)}</div>'
        "</div>"
        for i, (label, value, unit) in enumerate(pairs)
    )
    center_top = size / 2 - tokens["types"]["stat_sm"] * 0.85
    return (
        '<div class="cols" style="align-items:center;gap:52px;flex:1 1 auto;min-height:0">'
        f'<div class="donut-wrap" style="width:{size}px;height:{size}px">{svg}'
        f'<div class="donut-center" style="top:{center_top:.0f}px">'
        f'<div class="stat-num" style="font-size:{tokens["types"]["stat_sm"]}px">{share:.0f}<span class="stat-unit">%</span></div>'
        f'<div class="small">{esc(lead_label[:10])}</div></div>'
        "</div>"
        f'<div class="chart-legend">{legend}</div>'
        "</div>"
    )


def _arc(cx: float, cy: float, r: float, a0: float, a1: float) -> str:
    """圆环扇形路径（外弧 + 直线回中心）。角度制，0° 指向三点钟方向。"""
    if a1 - a0 >= 359.999:
        return f"M {cx - r} {cy} a {r} {r} 0 1 0 {2 * r} 0 a {r} {r} 0 1 0 {-2 * r} 0 Z"
    x0 = cx + r * math.cos(math.radians(a0))
    y0 = cy + r * math.sin(math.radians(a0))
    x1 = cx + r * math.cos(math.radians(a1))
    y1 = cy + r * math.sin(math.radians(a1))
    large = 1 if (a1 - a0) > 180 else 0
    return f"M {cx:.2f} {cy:.2f} L {x0:.2f} {y0:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} Z"


def _fill(hue: str, tokens: dict, *, vertical: bool = False) -> str:
    """图形填充风格：flat 纯色 / gradient 同色相明度渐变 / outline 半透明中空。

    数据图形只做同色相渐变——跨色相会让读者误以为是第二个数据类别。
    跨色相的渐变留给封面背景。
    """
    style = tokens.get("graphic") or "auto"
    if style == "outline":
        return color.with_alpha(hue, 0.24)
    if style == "flat":
        return f"#{hue}"
    angle = 180 if vertical else 90
    light = color.blend(hue, "FFFFFF", 0.26) if tokens.get("mode") != "light" else color.blend(hue, "FFFFFF", 0.18)
    return f"linear-gradient({angle}deg, #{hue} 0%, #{light} 100%)"


def _timeline(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int, accent_index: int) -> str:
    bullets = clean_bullets(page, limit=6)
    items: list[str] = []
    for i, bullet in enumerate(bullets):
        when, what = dr._timeline_split(bullet)
        hue = tk.chart_hue(tokens, accent_index + i)
        rail = '<div class="tl-rail">'
        # 轨道要连到最后一个节点：首段从圆心起，末段到圆心止，中间段跨过条目间隙。
        # 线先画、点后画，保证圆点盖住线（pptx 侧按 DOM 顺序叠形状）
        top = "50%" if i == 0 else "0"
        bottom = "50%" if i == len(bullets) - 1 else "-12px"
        if len(bullets) > 1:
            rail += f'<div class="tl-line" style="top:{top};bottom:{bottom}"></div>'
        rail += f'<div class="tl-dot" style="background:#{hue}"></div></div>'
        items.append(
            f'<div class="tl-item">{rail}<div class="tl-band">'
            f'<div class="tl-time" style="--hue:{_ink_hue(tokens, accent_index + i)}">{esc(when)}</div>'
            f'<div class="tl-text">{highlight(what)}</div></div></div>'
        )
    main = f'<div class="timeline">{"".join(items)}</div>'
    return _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>' + main


def _quote(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int) -> str:
    """金句页：左侧强调竖轨 + 右侧引文与署名。

    不用超大引号字符做装饰——字形只占 em 框顶部三成，下面会留出一大洞，
    且淡到几乎看不见（实测对比度 1.2:1）。竖轨是真实 div，高度跟着引文走，
    映射到 pptx 就是一根圆角矩形，构图稳、也更好编辑。
    """
    bullets = clean_bullets(page, limit=3)
    text = bullets[0] if bullets else page.title
    by = bullets[1] if len(bullets) > 1 else (page.kicker or chapter)
    role = bullets[2] if len(bullets) > 2 else ""
    if not role and by and "·" in by:
        by, role = [part.strip() for part in by.split("·", 1)]
    attribution = ""
    if by or role:
        attribution = '<div class="rule" style="width:132px"></div><div class="quote-credit">'
        if by:
            attribution += f'<div class="quote-by">{esc(by)}</div>'
        if role:
            attribution += f'<div class="quote-role">{esc(role)}</div>'
        attribution += "</div>"
    return (
        '<div class="quote grow">'
        f'<div class="quote-rail" style="background:#{tk.chart_hue(tokens, 0)}"></div>'
        '<div class="quote-body">'
        f'<div class="quote-text">{esc(text)}</div>'
        + attribution
        + "</div></div>"
    )


def _table_rows(bullets: list[str]) -> list[list[str]]:
    """表格素材：优先 `A | B | C`，其次全篇「表头：内容」两列。"""
    piped = [b for b in bullets if b.count("|") >= 1]
    if len(piped) >= 2:
        rows = [[cell.strip() for cell in b.split("|")] for b in piped]
        width = max(len(r) for r in rows)
        return [r + [""] * (width - len(r)) for r in rows][:8]
    pairs = [dr._card_split(b) for b in bullets]
    if len(pairs) >= 3 and all(head for head, _ in pairs):
        return [["项目", "说明"]] + [[head, body] for head, body in pairs][:7]
    return []


def _table(page: OutlinePage, outline: Outline, tokens: dict, chapter: str, variant: int) -> str:
    rows = _table_rows(clean_bullets(page, limit=8))
    if not rows:
        return _bullets(page, outline, tokens, chapter, variant)
    head, body = rows[0], rows[1:]
    html_rows = [
        '<div class="dtr">' + "".join(f'<div class="dth">{esc(c)}</div>' for c in head) + "</div>"
    ]
    for row in body:
        html_rows.append(
            '<div class="dtr">' + "".join(f'<div class="dtd">{highlight(c)}</div>' for c in row) + "</div>"
        )
    main = f'<div class="dtable">{"".join(html_rows)}</div>'
    return _head(page, chapter, tokens) + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>' + main


def _end(page: OutlinePage, outline: Outline, tokens: dict, variant: int) -> str:
    bullets = clean_bullets(page, limit=2)
    title = page.title or "谢谢"
    sub = bullets[0] if bullets else (outline.subtitle or "")
    title_px = _display_px(title, tokens, tokens["canvas_w"] - tokens["margin"] * 2)
    return (
        '<div class="end-wrap" style="' + _cover_text_vars(tokens) + '">'
        f'<div class="kicker">{esc(page.kicker or "THANK YOU")}</div>'
        f'<h1 class="end-title" style="font-size:{title_px}px">{esc(title)}</h1>'
        + (f'<div class="end-sub" style="max-width:{int(tokens["canvas_w"] * 0.6)}px">{esc(sub)}</div>' if sub else "")
        + "</div>"
    )


def _appendix_capacity(tokens: dict) -> int:
    """一页能放下几条来源。

    条目最少 44px（标题 23px、行高 1.5，再留一点呼吸），页头与间隙固定占掉
    约 180px。来源多时按容量分页，而不是把最后几条挤出画布。
    """
    room = tokens["canvas_h"] - 2 * tokens["margin"] - 180
    return max(3, int(room // 56))


def _appendix(sources: list, tokens: dict, index: int, total: int,
              part: tuple[int, int] = (1, 1)) -> str:
    items = "".join(
        '<div class="apx-item">'
        f'<div class="apx-no">{i + 1:02d}</div>'
        f'<div class="apx-title">{esc((s.title or s.kb_name or "未命名来源")[:40])}</div>'
        f'<div class="apx-url">{esc((s.url or s.kb_name or "")[:64])}</div>'
        "</div>"
        for i, s in enumerate(sources)
    )
    title = "来源与参考" + (f"（{part[0]}/{part[1]}）" if part[1] > 1 else "")
    head = (
        '<div class="head"><div class="kicker">REFERENCES</div>'
        f'<h1 class="h1">{title}</h1><div class="rule"></div></div>'
    )
    return (
        head
        + f'<div class="spacer" style="height:{tokens["gap"]}px"></div>'
        + f'<div class="appendix">{items}</div>'
        + f'<div class="footer"><div class="foot-no">{index + 1:02d} / {total:02d}</div>'
        '<div class="foot-src">本页由平台自动汇总</div></div>'
    )


# ---------------------------------------------------------------- 对外入口


@dataclass
class PageOverride:
    """lint 修复对单页的构图覆写。只动构图，不动用户确认过的文案。"""

    layout: str = ""
    scale: float = 1.0
    drop: int = 0

    def extra_css(self, tokens: dict) -> str:
        """本页字阶与间距按倍率收紧。变量挂在 .slide 上，只影响这一页。"""
        if abs(self.scale - 1.0) < 0.005:
            return ""
        lines = [
            f"--t-{name.replace('_', '-')}:{max(12, round(size * self.scale))}px;"
            for name, size in tokens["types"].items()
        ]
        lines.append(f"--gap:{max(12, round(tokens['gap'] * self.scale))}px;")
        lines.append(f"--pad:{max(12, round(tokens['pad'] * self.scale))}px;")
        return ".slide{" + "".join(lines) + "}"


def compose_deck(outline: Outline, tokens: dict, plan: tk.DeckPlan | None = None,
                 overrides: dict[int, PageOverride] | None = None) -> list[SlideHtml]:
    """整份文稿 → 每页 HTML 文档。附录页按 sources_policy 追加。

    overrides 是 lint 修复回写的单页覆写（换版式 / 收字阶 / 减要点），
    只重算被点名的页，其余页保持原样——修复不该让整份文稿跟着抖。
    """
    plan = plan or plan_deck(outline, tokens)
    overrides = overrides or {}
    pages = outline.flatten_pages()
    total = len(pages) + (1 if outline.has_appendix() else 0)
    chapter_of: dict[str, tuple[str, int, list[str]]] = {}
    for ci, section in enumerate(outline.sections, start=1):
        titles = [p.title for p in section.pages if p.layout not in ("cover", "section", "end")]
        for page in section.pages:
            chapter_of[page.id] = (section.title, ci, titles)

    slides: list[SlideHtml] = []
    for index, page in enumerate(pages):
        override = overrides.get(index) or PageOverride()
        kind = override.layout or (plan.layouts[index] if index < len(plan.layouts) else "bullets")
        variant = plan.variants[index] if index < len(plan.variants) else 0
        accent_index = plan.accents[index] if index < len(plan.accents) else index
        if override.drop > 0 and len(page.bullets) > override.drop:
            page = page.model_copy(deep=True)
            page.bullets = page.bullets[: max(2, len(page.bullets) - override.drop)]
        chapter, chapter_no, chapter_titles = chapter_of.get(page.id, ("", 1, []))
        canvas = _canvas(kind, page, outline, tokens, chapter, chapter_no, chapter_titles,
                         index, variant, accent_index)
        footer = "" if kind in ("cover", "section", "end") else _footer(page, outline, index, total, tokens)
        body = _motif(tokens, kind, variant) + f'<div class="canvas">{canvas}</div>' + footer
        slides.append(
            SlideHtml(
                index=index, kind=kind, title=page.title or outline.title,
                html=render_document(body, tokens, kind=kind, extra_css=override.extra_css(tokens)),
                chapter=chapter, notes=page.note or "",
            )
        )
    if outline.has_appendix():
        sources = list(outline.appendix_sources())
        size = _appendix_capacity(tokens)
        chunks = [sources[i:i + size] for i in range(0, len(sources), size)] or [[]]
        for offset, chunk in enumerate(chunks):
            index = len(pages) + offset
            part = (offset + 1, len(chunks))
            body = (_motif(tokens, "appendix", 0)
                    + f'<div class="canvas">{_appendix(chunk, tokens, index, total, part)}</div>')
            slides.append(
                SlideHtml(
                    index=index, kind="appendix",
                    title="来源与参考" + (f"（{offset + 1}）" if len(chunks) > 1 else ""),
                    html=render_document(body, tokens, kind="appendix"),
                )
            )
    return slides


def _canvas(kind: str, page: OutlinePage, outline: Outline, tokens: dict, chapter: str,
            chapter_no: int, chapter_titles: list[str], index: int, variant: int, accent_index: int) -> str:
    if kind == "cover":
        return _cover(page, outline, tokens, variant)
    if kind == "section":
        return _section(page, outline, tokens, index, chapter_titles, chapter_no)
    if kind == "end":
        return _end(page, outline, tokens, variant)
    if kind == "two_col":
        return _two_col(page, outline, tokens, chapter, variant)
    if kind == "cards":
        return _cards(page, outline, tokens, chapter, variant, accent_index)
    if kind == "stat":
        return _stat(page, outline, tokens, chapter, variant, accent_index)
    if kind == "metric":
        return _metric_page(page, outline, tokens, chapter, variant, accent_index)
    if kind == "chart":
        return _chart(page, outline, tokens, chapter, variant, accent_index)
    if kind == "timeline":
        return _timeline(page, outline, tokens, chapter, variant, accent_index)
    if kind == "quote":
        return _quote(page, outline, tokens, chapter, variant)
    if kind == "table":
        return _table(page, outline, tokens, chapter, variant)
    return _bullets(page, outline, tokens, chapter, variant)
