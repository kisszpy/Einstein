"""逐页重排：让技能在「可映射的组件词表」里重新组织这一页，而不是自由画布。

为什么不是自由 HTML：下游是「实测 DOM → 原生形状」。模型一旦写出自己的类名或
内联样式，pptx 里就没有对应的填充/字号规则，页会散架。所以这里把它限制在
已有的组件类里——它选的是**构图**，不是重写设计系统。

三道闸门，任何一道不过就回退到合成器那一版：
1. 结构校验：只准白名单标签与类名，不许 style / img / svg / script；
2. 文字保全：原文每一条要点都得在结果里出现；
3. 实测体检：重排后越界、重叠、字号地板一个都不能退步。
"""

from __future__ import annotations

import asyncio
import json
import re
from html.parser import HTMLParser

from app.core.logging import get_logger
from app.services.ppt_studio.htmlkit.compose import SlideHtml, esc
from app.services.ppt_studio.htmlkit.stylesheet import COMPONENT_CLASSES

logger = get_logger(__name__)

# 允许的标签：都是块级/行内的纯结构，pptx 侧能落成文本框或形状
ALLOWED_TAGS = {"div", "span", "p", "h1", "h2", "h3", "blockquote"}
ALLOWED_ATTRS = {"class"}
MAX_CHARS = 6000

# 给模型看的词表：只列内容页真用得上的组件，避免它去拼装饰
VOCAB = {
    "canvas": "页面内容区（版心），必须作为最外层",
    "head/kicker/h1/rule": "页头：小标 + 标题 + 分隔线（由调用方固定，不要重复）",
    "cols/col": "横向分栏容器 / 一栏",
    "card": "卡片（内边距、边框、圆角）；card--flat 交替底色；card--accent 左侧强调",
    "card-top": "卡片上部纵向堆叠",
    "h2/h3/lead/body/small": "副标题 / 小标题 / 导语 / 正文 / 注释",
    "bullets/bullets--band": "要点流；band 变体是实底横幅",
    "b-item/b-band/b-no/b-text": "条目 / 横幅 / 序号 / 条目正文",
    "stats/stat/stat-num/stat-unit/stat-label/stat-note": "大数字行与单卡",
    "metric-*": "指标轨道（metric-spot 聚焦、metrics 仪表盘、metric-row 一行）",
    "chart-wrap/chart-head/bar-row/col-item/lg-item": "图表容器与图元",
    "quote/quote-body/quote-text/quote-by": "引用",
    "dtable/dtr/dth/dtd": "表格",
    "spacer/grow": "固定间距 / 吃掉剩余空间",
}

SYSTEM = """\
你在为一个已经定好视觉系统的幻灯片重排**一页**的内容区。

# 你只能做构图
- 只准使用给你的组件类，不许发明新类、不许写 style 属性；
- 不许改文字：每一条要点的文字必须原样出现在结果里（可以拆分到多个节点，
  但字不能变、不能删、不能加）；
- 不许写 img / svg / script / style / a；没有图片素材；
- **页头与页脚由调用方负责**：不要在内容里重复页标题、小标、页码或来源，
  否则整页会出现两遍标题。

# 目标
这一页已经被确定性合成器排过一版，几何上没问题但很可能平庸：三张等权卡片、
一列均匀的要点、或者所有内容一样重。请用同一批组件重新组织，做出**主次**：
- 一个视觉落点（最大的数字、最重的那句、或最强的对比）；
- 分组的疏密：同组更近，异组更远；
- 不要四平八稳地把所有条目排成等宽等高。

# 输出契约
严格只输出一个 JSON 对象：

```
{"html": "<div class=\\"canvas\\">…</div>", "why": "一句话说明你做了什么构图决策"}
```

`html` 是**内容区内部**的 HTML（**不要**包含最外层 `div.canvas`，页头由调用方加）。不要输出 markdown 围栏。
"""


class _Checker(HTMLParser):
    """结构校验：标签/类名/属性白名单。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"标签 <{tag}> 不可用")
            return
        for name, value in attrs:
            if name not in ALLOWED_ATTRS:
                self.problems.append(f"属性 {name} 不可用")
                continue
            if name == "class":
                for cls in (value or "").split():
                    if cls not in COMPONENT_CLASSES:
                        self.problems.append(f"类名 .{cls} 不在设计系统里")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        self.text_parts.append(data)


def validate(html: str, bullets: list[str], title: str = "") -> tuple[bool, str]:
    """结构 + 文字两道闸门。返回 (通过?, 原因)。"""
    if not html or len(html) > MAX_CHARS:
        return False, f"长度不合适（{len(html)} 字符）"
    checker = _Checker()
    try:
        checker.feed(html)
        checker.close()
    except Exception as exc:  # noqa: BLE001 - 解析失败就是不合格
        return False, f"HTML 解析失败：{exc}"
    if checker.problems:
        return False, "；".join(dict.fromkeys(checker.problems))[:200]
    rendered = re.sub(r"\s+", "", "".join(checker.text_parts))
    # 页头由调用方加：内容里再出现一次标题就是重复
    if title and len(title) >= 6 and re.sub(r"\s+", "", title) in rendered:
        return False, "内容里重复了页标题"
    for bullet in bullets:
        needle = re.sub(r"\s+", "", bullet)
        if needle and needle not in rendered:
            return False, f"文字被改动或丢失：{bullet[:24]}"
    return True, ""


def build_brief(page, tokens: dict, card: str) -> str:
    vocab = "\n".join(f"- .{cls}：{role}" for cls, role in VOCAB.items() if "/" not in cls)
    checks = "、".join(VOCAB[key] for key in ("cols/col", "card", "bullets/bullets--band", "metric-*"))
    return (
        f"【设计系统】\n{card}\n\n"
        f"【可用组件（括号里是它控制的排布）】\n{vocab}\n"
        f"分栏/卡片/要点/指标的组合方式：{checks}\n\n"
        f"【这一页】标题：{page.title or '（无）'}\n"
        + "\n".join(f"- {item}" for item in page.bullets)
        + "\n\n请输出重排后的 JSON。"
    )


def _ask(system: str, brief: str, ws: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.services.llm import stream_chat

    async def run() -> str:
        buf: list[str] = []
        async for kind, text in stream_chat(
            [SystemMessage(system), HumanMessage(brief)],
            temperature=0.35,
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
    raise RuntimeError("重排不能在事件循环线程里同步调用")


def design_page(page, tokens: dict, card: str, ws: str) -> tuple[str, str] | None:
    """返回 (body_html, why)；不合格返回 None。"""
    from app.services.ppt_studio.htmlkit import skill_bridge

    skill = skill_bridge.resolve(ws, command="layout")
    system = (skill.system + "\n\n" if skill else "") + SYSTEM
    try:
        raw = _ask(system, build_brief(page, tokens, card), ws)
    except Exception as exc:  # noqa: BLE001 - 重排失败就保留合成器版本
        logger.warning("逐页重排请求失败：%s", exc)
        return None
    text = re.sub(r"```(?:json)?|```", "", raw or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    html = str(data.get("html") or "").strip()
    ok, reason = validate(html, [b for b in page.bullets if b and b.strip()], page.title or "")
    if not ok:
        logger.info("逐页重排未通过校验：%s", reason)
        return None
    return html, str(data.get("why") or "")[:80]


__all__ = ["validate", "design_page", "build_brief", "SYSTEM", "VOCAB", "ALLOWED_TAGS"]