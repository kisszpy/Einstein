"""第三方设计检测器（impeccable，Apache-2.0）的适配层。

它和 `lint` 是两套眼睛，看的不是同一件事：

- `lint` 量的是**几何**：越界、重叠、文字溢出、字号地板——排版事故。
- 这里量的是**品味**：对比度、行长、行高、孤字、字阶层次、AI 腔，以及
  「章节小标 + 序号 + 侧边色条」这类一眼就认出是模型生成的套路。

为什么不用它的静态 HTML 引擎：我们的样式表全靠 CSS 变量，静态解析器读不出
`var(--margin)`，会把整页误报成「内容贴边」。走 `file://` URL 让它用真浏览器
算计算样式，规则才准，顺带给出 `selector`——润色层能精确指着元素改。

任何一步失败都返回空报告并记原因：检测器是加分项，不能把生成拖下来。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.services.ppt_studio.htmlkit.compose import SlideHtml
from app.services.ppt_studio.htmlkit.tokens import CANVAS_H, CANVAS_W

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_VENDORED = _REPO_ROOT / "tools" / "impeccable" / "bin"
_TIMEOUT_S = 180
# 每次扫描都会自己拉起一个 Chromium（~200MB）。和实测层共用一个闸门会让设计台
# 卡住，这里单独限 1 个：扫描是整份一次，排队 30 秒还不来就让调用方跳过。
_SLOTS = threading.BoundedSemaphore(1)
_SLOT_WAIT_S = 30

# 固定画布、静态、无交互——这些规则在幻灯片上要么恒定误报，要么永远不触发。
_NOISE = {
    "script-error": "幻灯片没有脚本",
    "broken-image": "本引擎不出位图",
    "bounce-easing": "幻灯片是静态的，没有缓动",
    "pulsing-dot": "幻灯片是静态的，没有动画",
    "blinking-cursor": "幻灯片没有光标",
    "marquee": "幻灯片没有滚动",
    "undersized-ui-text": "幻灯片没有可点控件",
    "edge-flush-cards": "没有横向滚动容器",
    "first-viewport-column-overflow": "没有滚动视口",
    "body-text-viewport-edge": "页面即画布，版心由 --margin 控制，不是视口留白",
    "content-hidden-at-rest": "没有脚本控制显隐",
    "clipped-overflow-container": "画布裁切是刻意的（overflow:hidden）",
    "design-system-font": "本引擎的设计系统是 tokens，不是 DESIGN.md",
    "design-system-color": "同上",
    "design-system-radius": "同上",
    "design-system-font-size": "同上",
}

# 配色来自用户模板时，「像不像 AI 生成」这类判断失去依据
_BRAND_RULES = {"ai-color-palette", "cream-palette"}

# 真该改的硬伤：出现就不该出货。其余按建议处理。
_HARD = {
    "low-contrast",
    "tiny-text",
    "text-overflow",
    "text-occlusion",
    "cramped-padding",
    "tight-leading",
    "nested-cards",
    "gray-on-color",
}

_NAMES = {
    "low-contrast": "对比度不足",
    "tiny-text": "正文字号过小",
    "text-overflow": "文字溢出容器",
    "text-occlusion": "文字被压住",
    "cramped-padding": "内边距过窄",
    "tight-leading": "行高过紧",
    "line-length": "行长不当",
    "nested-cards": "卡中卡",
    "monotonous-spacing": "间距没有节奏",
    "heading-rhythm": "标题与上文的间距不够",
    "straggler": "行尾孤字",
    "unbalanced-heading": "标题折行失衡",
    "oversized-h1": "标题过大",
    "flat-type-hierarchy": "字阶没有层次",
    "wide-tracking": "正文过宽的字距",
    "extreme-negative-tracking": "字距压得过紧",
    "justified-text": "两端对齐",
    "all-caps-body": "正文全大写",
    "skipped-heading": "标题层级跳跃",
    "kicker-above-heading": "标题上方多一行小标",
    "numbered-section-labels": "序号小标",
    "side-tab": "卡片侧边色条",
    "border-accent-on-rounded": "圆角配粗描边",
    "hero-eyebrow-chip": "眉标胶囊",
    "icon-tile-stack": "图标块叠在标题上",
    "gray-on-color": "彩色底上用灰字",
    "overused-font": "烂大街的字体",
    "gradient-text": "渐变文字",
    "ai-color-palette": "AI 配色（紫蓝渐变）",
    "cream-palette": "奶油米色调色板",
    "italic-serif-display": "斜体衬线大标题",
    "radial-halo": "径向光晕背景",
    "radial-spotlight-glow": "聚光式辉光",
    "dark-glow": "暗色发光阴影",
    "hard-offset-shadow": "硬偏移投影",
    "organic-clip-path": "clip-path 伪有机轮廓",
    "shape-assembled-illustration": "拼图形凑插画",
    "buried-raster": "位图被蒙层盖住",
    "gpt-thin-border-wide-shadow": "细描边配宽阴影",
    "repeating-stripes-gradient": "条纹渐变背景",
    "codex-grid-background": "装饰性网格背景",
    "repeated-container-text": "同一容器里重复文字",
    "marketing-buzzword": "营销术语",
    "aphoristic-cadence": "格言式排比",
    "theater-slop-phrase": "戏剧化空话",
    "em-dash-overuse": "破折号泛滥",
}

# 品味的轻重：这些是「看着像模型随手生成」的套路，值得改，但不该拦出货。
_SOFT = set(_NAMES) - _HARD


@dataclass
class Finding:
    rule: str
    name: str
    detail: str
    selector: str = ""
    slide: int = -1
    severity: str = "warn"

    def payload(self) -> dict:
        return {
            "rule": self.rule,
            "name": self.name,
            "detail": self.detail,
            "selector": self.selector,
            "slide": self.slide,
            "severity": self.severity,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    skipped: str = ""

    @property
    def hard(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def summary(self) -> dict:
        by_rule: dict[str, int] = {}
        for finding in self.findings:
            by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1
        return {
            "total": len(self.findings),
            "errors": len(self.hard),
            "warnings": len(self.findings) - len(self.hard),
            "by_rule": by_rule,
            "skipped": self.skipped,
        }

    def payload(self) -> list[dict]:
        return [finding.payload() for finding in self.findings]


def locate() -> Path | None:
    """按「显式指定 → 仓库内 vendored → 用户缓存 → PATH」找引擎。"""
    for env_key in ("EINSTEIN_IMPECCABLE_BIN", "IMPECCABLE_BIN"):
        value = os.environ.get(env_key)
        if value and Path(value).is_file():
            return Path(value)
    for rel in ("impeccable.exe", "impeccable"):
        candidate = _VENDORED / rel
        if candidate.is_file():
            return candidate
    home = Path(os.environ.get("IMPECCABLE_HOME") or Path.home() / ".impeccable")
    for candidate in sorted((home / "bin").glob("*/impeccable*"), reverse=True):
        if candidate.is_file():
            return candidate
    found = shutil.which("impeccable")
    return Path(found) if found else None


def available() -> bool:
    return locate() is not None


def unavailable_reason() -> str:
    return (
        "未找到 impeccable 引擎：执行 python tools/impeccable/fetch.py 下载，"
        "或设置 EINSTEIN_IMPECCABLE_BIN 指向已有二进制"
    )


def inspect(slides: list[SlideHtml], out_dir: Path, *,
            canvas: tuple[int, int] = (CANVAS_W, CANVAS_H),
            timeout_s: int = _TIMEOUT_S, brand_sourced: bool = False) -> Report:
    """整份一次扫描。返回按页归并的 findings；不可用时返回带原因的旧报告。

    brand_sourced：配色来自用户上传的模板。这时「AI 配色」「奶油色板」是彻头彻尾的
    误报——那正是用户公司的品牌色，检测器无权指认它像模型生成的。
    """
    if not slides:
        return Report(skipped="没有可检测的页面")
    binary = locate()
    if binary is None:
        return Report(skipped=unavailable_reason())
    if not _SLOTS.acquire(timeout=_SLOT_WAIT_S):
        return Report(skipped="美感检测器繁忙（等待超过 30 秒）")
    try:
        return _scan(binary, slides, out_dir, canvas, timeout_s, brand_sourced)
    except Exception as exc:  # noqa: BLE001 - 检测器永不阻断生成
        logger.warning("美感检测失败，跳过：%s", exc)
        return Report(skipped=f"检测失败：{exc}")
    finally:
        _SLOTS.release()


def _scan(binary: Path, slides: list[SlideHtml], out_dir: Path,
          canvas: tuple[int, int], timeout_s: int, brand_sourced: bool) -> Report:
    work = out_dir / "aesthetic"
    work.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []
    for slide in slides:
        path = work / f"{slide.index:02d}.html"
        path.write_text(slide.html, encoding="utf-8")
        urls.append(path.as_uri())

    env = dict(os.environ)
    # 三件事必须关死：遥测、Pro 分派（会尝试联网换二进制）、用户账号目录。
    # 这是服务端进程，不该带着个人身份状态，也不该在生成路径上打外网。
    env["DO_NOT_TRACK"] = "1"
    env["IMPECCABLE_NO_TELEMETRY"] = "1"
    env["IMPECCABLE_PRO"] = "off"
    env["IMPECCABLE_HOME"] = str(work / ".home")
    proc = subprocess.run(
        [str(binary), "detect", "--json", "--no-config",
         "--viewport", f"{canvas[0]}x{canvas[1]}", *urls],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout_s, env=env,
    )
    # 0 = 干净，2 = 有 findings，1 = 有目标没扫成。只有 0/2 能解析出结果。
    if proc.returncode not in (0, 2):
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"引擎退出码 {proc.returncode}：{detail or '无输出'}")

    raw = json.loads(proc.stdout or "[]")
    findings: list[Finding] = []
    for item in raw:
        rule = str(item.get("antipattern") or "")
        if not rule or rule in _NOISE or (brand_sourced and rule in _BRAND_RULES):
            continue
        slide_index = _slide_index(item.get("file") or "")
        severity = "error" if rule in _HARD else "warn"
        findings.append(
            Finding(
                rule=rule,
                name=_NAMES.get(rule, rule),
                detail=_detail(item),
                selector=str(item.get("selector") or ""),
                slide=slide_index,
                severity=severity,
            )
        )
    findings.sort(key=lambda f: (f.slide, f.severity != "error", f.rule))
    logger.info("美感检测：%d 页，%d 条 findings（%d 条硬伤）",
                len(slides), len(findings), sum(1 for f in findings if f.severity == "error"))
    return Report(findings=findings)


def _slide_index(file: str) -> int:
    """`…/03.html` → 3。扫描目录里的文件名就是页号，解析不出就记 -1。"""
    stem = Path(file).stem if file else ""
    return int(stem) if stem.isdigit() else -1


def _detail(item: dict) -> str:
    snippet = str(item.get("snippet") or "").strip()
    text = str(item.get("description") or "").strip()
    # 描述是整条规则的说明，每条都一样；真正说明「这一处」的是 snippet。
    # 只留第一句，报告里才读得下去。
    head = text.split(" — ")[0].split(". ")[0].strip()
    if snippet and head:
        return f"{snippet}｜{head}"
    return snippet or head


__all__ = ["Finding", "Report", "available", "unavailable_reason", "locate", "inspect"]