"""选视觉方向的提示词：技能给判断，代码给取值范围。"""

from __future__ import annotations

from app.services.ppt_studio.outline import Outline

RUNTIME = """\
【本次执行环境】
你在一个无头流水线里，只做一件事：为这份演示文稿定一个视觉方向。
- 没有文件读写、没有 shell、没有浏览器；`impeccable context` 已由调用方执行；
- 不需要也不允许输出任何代码、HTML 或十六进制色值——方向用参数表达，
  具体配色由调用方按这些参数生成并保证对比度。
"""

JOB = """\
【本轮的判断对象】
一份即将生成的幻灯片。它的读者、章节和每页要点都在下面的【大纲】里。
按技能里「选择视觉世界」的方法判断这份内容属于哪种气质：
它是制造业年报、政策白皮书，还是消费品牌提案？读者在什么场合看？投影还是打印？
从读者熟悉的那个世界里取质感——不要取这个品类默认的样子。

【输出参数的含义】
- mode：dark（投影更稳、数据更压得住）或 light（打印、阅读感、正式文书）。
- hue：主色相，0~359。避开 250~320 的紫——那是模型生成的头号指纹。
- chroma：饱和度，0.10（极克制）~0.52（强主张）。年报/白皮书偏低，消费提案偏高。
- hue2：副色相，要与 hue 相差 40 度以上，用于图表第二色和封面第二段渐变。
- density：loose（少字、重留白）| normal | tight（数据密、信息多）。
- motif：none 无装饰 | rule 贯通细线 | grid 细线网格 | index 章节编号 | glass 柔和面块。

【输出契约】严格只输出一个 JSON 对象，不要解释：

```
{
  "name": "方向名，2~6 个汉字，像设计工作室给方案起的名字",
  "thesis": "一句话：从哪取质感、为什么适合这份内容（不超过 60 字）",
  "mode": "dark",
  "hue": 208,
  "chroma": 0.34,
  "hue2": 24,
  "density": "normal",
  "motif": "grid"
}
```

方向名不要用「商务」「科技」「高端」这类空词，它要能让人想起一个具体的东西或场所。
"""

FALLBACK = """\
你是一位艺术指导，要为一份演示文稿定视觉方向。按内容的气质选明暗、主色相、
饱和度、副色相、疏密和母题装饰，输出 JSON。避开紫色系与任何低对比的糊色搭配。
色相要从读者熟悉的那个世界里取，不是从「科技公司都这样」里取。
"""


def compose(skill_system: str | None) -> str:
    body = skill_system or FALLBACK
    return f"{RUNTIME}\n{body}\n\n{JOB}"


def brief(outline: Outline) -> str:
    """大纲摘要：够判断气质，不把全文塞进去。"""
    parts = [
        "【大纲】",
        f"标题：{outline.title or '（无）'}",
    ]
    if outline.subtitle:
        parts.append(f"副标题：{outline.subtitle}")
    if outline.audience:
        parts.append(f"读者：{outline.audience}")
    for index, section in enumerate(outline.sections, start=1):
        pages = "；".join(
            (page.title or "") for page in section.pages if page.title
        )
        parts.append(f"{index}. {section.title or '未命名章'}：{pages}")
    sample = next(
        (page.bullets[:2] for section in outline.sections for page in section.pages
         if page.bullets),
        [],
    )
    if sample:
        parts.append("【首条要点的措辞，用来看语气】")
        parts.extend(f"- {item}" for item in sample)
    return "\n".join(parts)


__all__ = ["RUNTIME", "JOB", "FALLBACK", "compose", "brief"]