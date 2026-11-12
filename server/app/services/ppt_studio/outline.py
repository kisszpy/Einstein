"""确认卡（Outline）的数据模型与校验——一份模型双用：前端可编辑树 ←→ 生成输入。

校验以后端为准（`PUT /outline` 与 `generate` 入口都过 `validate()`），
前端只做轻量提示，避免两套规则漂移。页数口径写死在后端：
`page_count` = 内容页（flatten_pages），`total_slides` = 内容页 + 来源附录页。
"""

from __future__ import annotations

import re
import uuid
from pydantic import BaseModel, Field

# 页面版式语义名（layout_resolved 是前端选定的具体版式名，不在枚举里）
PAGE_LAYOUTS = ("cover", "section", "bullets", "two_col", "quote", "table", "stat", "metric", "chart", "cards", "timeline", "end", "auto")
SOURCE_POLICIES = ("page", "appendix", "both", "off")
PRESET_THEMES = ("midnight", "graphite", "ocean", "sunset", "forest", "ivory")

TITLE_MAX = 80
SECTIONS_MAX = 8
PAGES_PER_SECTION_MAX = 8
PAGE_COUNT_MIN = 3
PAGE_COUNT_MAX = 30
BULLETS_MAX = 8
BULLET_MAX_LEN = 120

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _clamp(value: object, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class SourceRef(BaseModel):
    """一条来源：web（外链）或 kb（知识库文档）。"""

    id: str = ""
    kind: str = "web"  # web | kb
    title: str = ""
    url: str = ""
    kb_name: str = ""
    snippet: str = ""


class StyleSpec(BaseModel):
    """风格三选一：主题包 > 内置 preset > 语气微调，外加设计台旋钮。

    旋钮口径与 htmlkit.tokens.Knobs 一一对应（那边负责 clamp 与派生），
    这里只声明字段，让确认卡能把用户拧过的设计一起存下来——重新生成
    或翻历史时看到的还是同一版设计，而不是回到默认值。
    """

    theme_pack_id: str = ""
    preset_theme: str = ""
    tone: str = ""
    density: str = "normal"  # loose | normal | dense
    type_scale: float = 1.0  # 0.88 ~ 1.14 字阶总倍率
    radius: int = 14  # 0 ~ 28 卡片圆角
    whitespace: float = 1.0  # 0.8 ~ 1.25 版心外边距倍率
    accent: str = ""  # 强调色覆盖（空 = 用家族色）
    graphic: str = "auto"  # auto | flat | gradient | outline
    motif: bool = True  # 母题装饰
    footer: bool = True  # 页脚页码与来源
    seed: int = 0  # 构图变体种子（「随机一版」只动它）


class OutlinePage(BaseModel):
    id: str = ""
    title: str = ""
    kicker: str = ""
    layout: str = "auto"
    bullets: list[str] = Field(default_factory=list)
    note: str = ""
    sources: list[SourceRef] = Field(default_factory=list)
    layout_resolved: str = ""


class OutlineSection(BaseModel):
    id: str = ""
    title: str = ""
    pages: list[OutlinePage] = Field(default_factory=list)


class Outline(BaseModel):
    title: str = ""
    subtitle: str = ""
    audience: str = ""
    lang: str = "zh"
    page_count: int = 0
    style: StyleSpec = Field(default_factory=StyleSpec)
    sources_policy: str = "both"
    version: int = 1
    sections: list[OutlineSection] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)

    # ------------------------------------------------ 规范化

    def normalize(self) -> "Outline":
        """补 id、layout 归一、sources 去重——出卡与用户编辑后都要过一遍。"""
        if not self.title:
            self.title = ""
        # 标题超限只截断并提示，不拦（用户已编辑的内容不能丢）
        if len(self.title) > TITLE_MAX:
            self.title = self.title[:TITLE_MAX]
        if self.sources_policy not in SOURCE_POLICIES:
            self.sources_policy = "both"
        if self.style.preset_theme and self.style.preset_theme not in PRESET_THEMES:
            self.style.preset_theme = ""
        if self.style.density not in ("loose", "normal", "dense"):
            self.style.density = "normal"
        # 设计台旋钮在这里就 clamp：确认卡是「用户编辑过的东西」，
        # 前端滑到极端或被手改 JSON 都不能把非法值带进渲染层。
        self.style.type_scale = _clamp(self.style.type_scale, 0.88, 1.14, 1.0)
        self.style.whitespace = _clamp(self.style.whitespace, 0.8, 1.25, 1.0)
        self.style.radius = int(_clamp(self.style.radius, 0, 28, 14))
        self.style.seed = int(_clamp(self.style.seed, 0, 9999, 0))
        if self.style.graphic not in ("auto", "flat", "gradient", "outline"):
            self.style.graphic = "auto"
        if self.style.accent and not _HEX_COLOR.match(self.style.accent):
            self.style.accent = ""

        global_ids: set[str] = set()
        deduped_sources: list[SourceRef] = []
        for ref in self.sources:
            if not ref.id:
                ref.id = _new_id("s")
            if ref.id in global_ids:
                continue
            global_ids.add(ref.id)
            deduped_sources.append(ref)
        self.sources = deduped_sources

        for section in self.sections:
            if not section.id:
                section.id = _new_id("sec")
            for page in section.pages:
                if not page.id:
                    page.id = _new_id("pg")
                if page.layout not in PAGE_LAYOUTS:
                    # 版式枚举外的值归一为 auto（生成时按语义自动选版式）
                    page.layout = "auto"
                page.sources = [s for s in page.sources if s.id in global_ids]

        # 页数字段与实际章节内容对齐：模型给的数字不可信，以 sections 为准
        self.page_count = self.content_page_count()
        return self

    # ------------------------------------------------ 校验

    def content_page_count(self) -> int:
        return sum(len(s.pages) for s in self.sections)

    def has_appendix(self) -> bool:
        """来源附录页是否会出现：policy 允许 且 确实有来源。"""
        return self.sources_policy in ("appendix", "both") and bool(self.sources)

    def total_slides(self) -> int:
        return self.content_page_count() + (1 if self.has_appendix() else 0)

    def validate(self) -> list[str]:
        """硬校验：返回错误码列表（空 = 通过），路由层转 400。"""
        errors: list[str] = []
        if not self.title.strip():
            errors.append("title_required")
        if not (1 <= len(self.sections) <= SECTIONS_MAX):
            errors.append("sections_range")
        else:
            for section in self.sections:
                if not (1 <= len(section.pages) <= PAGES_PER_SECTION_MAX):
                    errors.append("pages_per_section")
                    break
        if not (PAGE_COUNT_MIN <= self.content_page_count() <= PAGE_COUNT_MAX):
            errors.append("page_count_range")
        return errors

    def warnings(self) -> list[str]:
        """软告警：不拦截，生成时截断并记入 degradations。"""
        notes: list[str] = []
        for si, section in enumerate(self.sections, start=1):
            for pi, page in enumerate(section.pages, start=1):
                if len(page.bullets) > BULLETS_MAX:
                    notes.append(
                        f"第 {si} 章第 {pi} 页「{page.title or '未命名'}」要点 "
                        f"{len(page.bullets)} 条，超出 {BULLETS_MAX} 条将被截断"
                    )
                for bullet in page.bullets:
                    if len(bullet) > BULLET_MAX_LEN:
                        notes.append(
                            f"第 {si} 章第 {pi} 页有超过 {BULLET_MAX_LEN} 字的要点，将截断"
                        )
                        break
        return notes

    # ------------------------------------------------ 映射

    def flatten_pages(self) -> list[OutlinePage]:
        pages: list[OutlinePage] = []
        for section in self.sections:
            pages.extend(section.pages)
        return pages

    def collect_page_source_ids(self) -> list[str]:
        used: list[str] = []
        for page in self.flatten_pages():
            for ref in page.sources:
                if ref.id not in used:
                    used.append(ref.id)
        return used

    def appendix_sources(self) -> list[SourceRef]:
        """附录页要列的来源：页面实际引用的优先、其余补全。"""
        used = self.collect_page_source_ids()
        ordered = [s for s in self.sources if s.id in used]
        ordered += [s for s in self.sources if s.id not in used]
        return ordered


class OutlineValidationError(ValueError):
    """带错误码的确认卡校验失败，路由层按 400 返回。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{message}（{code}）")
        self.code = code


def ensure_valid(outline: Outline, *, theme_pack_visible: bool = True) -> None:
    """入口校验：validate() 错误码 → OutlineValidationError；主题包不可见单独判。"""
    errors = outline.validate()
    if "title_required" in errors:
        raise OutlineValidationError("title_required", "确认卡标题不能为空")
    if "sections_range" in errors:
        raise OutlineValidationError("sections_range", f"章节数量需在 1~{SECTIONS_MAX} 之间")
    if "pages_per_section" in errors:
        raise OutlineValidationError("pages_per_section", f"每章页数需在 1~{PAGES_PER_SECTION_MAX} 之间")
    if "page_count_range" in errors:
        raise OutlineValidationError(
            "page_count_range", f"内容页合计需在 {PAGE_COUNT_MIN}~{PAGE_COUNT_MAX} 页之间"
        )
    if outline.style.theme_pack_id and not theme_pack_visible:
        raise OutlineValidationError("theme_pack_not_found", "所选模板不可见或已删除")


def page_title_safe(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()
