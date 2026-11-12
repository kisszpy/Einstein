"""编排：确认卡 → HTML → 浏览器实测 → lint 修复 → 原生 pptx。

对外接口与 `DesignRenderer.render` 对齐（blob / total_slides / layouts_used /
degradations / fidelity），所以 generator 可以无缝切换，浏览器不可用时原样回落。

修复闭环只重算「体检不通过」的那几页：整份重排会让已经排好的页跟着抖，
也会把一次生成的耗时翻倍。每页最多走三步阶梯，走完仍不通过就接受现状并
记进 degradations——宁可留一条告警，也不能无限循环卡住生成。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.ppt_studio import design_renderer as dr
from app.services.ppt_studio.htmlkit import (
    compose, impeccable, lint, measure, playbook, polish, pptx_writer, recompose, tokens as tk,
)
from app.services.ppt_studio.outline import Outline

logger = get_logger(__name__)

# 一页最多修三次（收字阶 → 换版式 → 减要点），整份最多跑三轮
MAX_ROUNDS = 3
# 预览默认只实测这几页：设计台要「秒回」，全量实测留给正式生成
PREVIEW_PAGES = 6
# 逐页重排每页一次模型调用：只挑最值得变的几页，其余交给合成器
RECOMPOSE_PAGES = 2

PhaseHook = Callable[[str, int, str], None]


def available() -> bool:
    return measure.available()


def unavailable_reason() -> str:
    return measure.unavailable_reason()


def _sample_indices(total: int, limit: int) -> list[int]:
    """预览取样：首尾必取，中间等距。封面和结尾最能体现家族气质。"""
    if total <= limit:
        return list(range(total))
    picked = {0, total - 1}
    step = (total - 1) / float(limit - 1)
    for i in range(1, limit - 1):
        picked.add(int(round(i * step)))
    return sorted(picked)


def _has_cards(page) -> bool:
    return sum(1 for b in page.bullets if dr._card_split(b)[0]) >= 3


class HtmlRenderer:
    """内置风格的 HTML 渲染器。一个实例一次生成，不持有跨次状态。"""

    def __init__(self, preset: dict, knobs: tk.Knobs | None = None,
                 workspace_id: str = "") -> None:
        self.preset = preset
        self.knobs = knobs or tk.Knobs()
        self.tokens = tk.resolve_tokens(preset, self.knobs)
        self._canvas = (self.tokens["canvas_w"], self.tokens["canvas_h"])
        # 润色层要调模型，模型按工作区取密钥与额度
        self.workspace_id = workspace_id

    # ------------------------------------------------ 正式生成

    def render(self, template_bytes: bytes | None, outline: Outline,
               *, on_phase: PhaseHook | None = None) -> dict:
        """template_bytes 只为签名兼容——内置风格永远从空白画起。"""
        hook = on_phase or (lambda *_: None)
        hook("composing", 45, "编排版式")
        plan = compose.plan_deck(outline, self.tokens)
        slides = compose.compose_deck(outline, self.tokens, plan)
        pages = outline.flatten_pages()

        with tempfile.TemporaryDirectory(prefix="ppt-html-") as tmp:
            work = Path(tmp)
            hook("measuring", 55, "浏览器实测排版")
            measured = measure.measure(slides, work / "pass-0", canvas=self._canvas)
            by_index = {int(m["index"]): m for m in measured}
            reports = {
                index: lint.inspect(item, self.tokens, layout=slides[index].kind)
                for index, item in by_index.items()
            }
            overrides: dict[int, compose.PageOverride] = {}
            degradations: list[str] = []

            for round_no in range(1, MAX_ROUNDS + 1):
                bad = [i for i in sorted(reports) if not reports[i].ok]
                if not bad:
                    break
                hook("repairing", 55 + round_no * 6, f"修复 {len(bad)} 页排版")
                touched: list[int] = []
                for index in bad:
                    report = reports[index]
                    page = pages[index] if index < len(pages) else None
                    repair = lint.next_repair(report, has_cards=_has_cards(page) if page else False)
                    if repair is None:
                        continue
                    previous = overrides.get(index) or compose.PageOverride()
                    overrides[index] = compose.PageOverride(
                        layout=repair.layout or previous.layout,
                        scale=min(previous.scale, repair.scale),
                        drop=previous.drop + repair.drop,
                    )
                    report.repairs.append(repair.label())
                    touched.append(index)
                if not touched:
                    break
                # 合成是纯字符串拼接，整份重算比逐页重算便宜；只有实测才挑页
                rebuilt = compose.compose_deck(outline, self.tokens, plan, overrides)
                pending = [rebuilt[i] for i in touched]
                fixed = measure.measure(pending, work / f"pass-{round_no}", screenshot=True, canvas=self._canvas)
                for item in fixed:
                    index = int(item["index"])
                    by_index[index] = item
                    slides[index] = rebuilt[index]
                    fresh = lint.inspect(item, self.tokens, layout=slides[index].kind)
                    fresh.attempts = round_no
                    fresh.repairs = reports[index].repairs
                    reports[index] = fresh

            ordered = [by_index[i] for i in sorted(by_index)]
            for index, report in sorted(reports.items()):
                if report.ok or not report.repairs:
                    continue
                title = slides[index].title if index < len(slides) else ""
                degradations.append(
                    f"第 {index + 1} 页「{title[:24]}」已自动{'、'.join(report.repairs)}"
                )
            for index, report in sorted(reports.items()):
                if report.ok:
                    continue
                title = slides[index].title if index < len(slides) else ""
                detail = report.errors[0].detail if report.errors else ""
                degradations.append(f"第 {index + 1} 页「{title[:24]}」仍有排版告警：{detail}")

            # 构图：让技能在组件词表内重排少数几页。不合规/体检退步/文字丢失
            # 一律回退到合成器那一版——这一层也只加分。
            slides, by_index, reports, composition = self._recompose(
                slides, by_index, reports, pages, plan, outline, work, hook
            )

            # 优雅度：几何站住之后再谈好看。检测器不可用、模型不可用、补丁退步，
            # 任何一种都原样出货——这一层只加分，不减分。
            slides, by_index, reports, aesthetic = self._refine(
                slides, by_index, reports, work, hook
            )
            ordered = [by_index[i] for i in sorted(by_index)]
            degradations.extend(aesthetic.pop("notes", []))

            hook("writing", 82, "写出原生形状")
            deck_path = work / "deck.pptx"
            stats = pptx_writer.write_deck(
                ordered, self.tokens, deck_path, notes=[s.notes for s in slides]
            )
            blob = deck_path.read_bytes()
            thumbs = _thumbnails(ordered)

        summary = lint.summarize([reports[i] for i in sorted(reports)])
        fidelity = _fidelity(summary, degradations, self.preset, aesthetic)
        fidelity["lint"] = summary
        fidelity["pages"] = [reports[i].payload() for i in sorted(reports)]
        fidelity["aesthetic"] = aesthetic
        fidelity["composition"] = composition
        fidelity["native_shapes"] = stats["shapes"] + stats["text_lines"]
        logger.info("HTML 渲染完成：%d 页，体检 %d 页通过，修复 %d 次",
                    len(ordered), summary["clean_pages"], summary["repairs"])
        return {
            "blob": blob,
            "total_slides": len(ordered),
            "layouts_used": [s.kind for s in slides],
            "degradations": degradations,
            "fidelity": fidelity,
            "thumbnails": thumbs,
            "engine": "html",
        }

    # ------------------------------------------------ 逐页重排

    def _recompose(self, slides, by_index, reports, pages, plan, outline, work: Path,
                   hook: PhaseHook):
        """让技能重排少数内容页。返回 (slides, by_index, reports, 说明)。"""
        info = {"skill_pages": [], "fallbacks": []}
        untouched = (slides, by_index, reports, info)
        if not get_settings().ppt_skill_layout or not self.workspace_id:
            return untouched
        eligible = [
            index for index in sorted(reports)
            if index < len(slides) and index < len(pages)
            and slides[index].kind in ("bullets", "two_col", "cards")
            and reports[index].ok
        ][:RECOMPOSE_PAGES]
        if not eligible:
            return untouched

        card = playbook.design_card(self.tokens)
        accepted = False
        for index in eligible:
            hook("recomposing", 72, f"重排第 {index + 1} 页构图")
            page = pages[index]
            designed = recompose.design_page(page, self.tokens, card, self.workspace_id)
            if designed is None:
                info["fallbacks"].append(f"第 {index + 1} 页：结构校验或调用失败，保留原构图")
                continue
            body, why = designed
            # 母题与页脚必须原样保留：重排的是内容区，不是整页外壳
            variant = plan.variants[index] if index < len(plan.variants) else 0
            motif = compose._motif(self.tokens, slides[index].kind, variant)
            footer = compose._footer(page, outline, index, len(pages), self.tokens)
            head = compose._head(page, slides[index].chapter, self.tokens)
            html = compose.render_document(
                motif + '<div class="canvas">' + head
                + f'<div class="spacer" style="height:{self.tokens["gap"]}px"></div>'
                + body + "</div>" + footer,
                self.tokens, kind=slides[index].kind,
            )
            candidate = compose.SlideHtml(
                index=slides[index].index, kind=slides[index].kind, title=slides[index].title,
                html=html, chapter=slides[index].chapter, notes=slides[index].notes,
            )
            try:
                item = measure.measure([candidate], work / f"recompose-{index}",
                                       canvas=self._canvas)[0]
            except measure.MeasureError as exc:
                info["fallbacks"].append(f"第 {index + 1} 页：实测失败（{exc}）")
                continue
            report = lint.inspect(item, self.tokens, layout=candidate.kind)
            previous = reports[index]
            if len(report.errors) > len(previous.errors):
                info["fallbacks"].append(
                    f"第 {index + 1} 页：重排后多了 {len(report.errors)} 处硬伤，已回退"
                )
                continue
            # 这里不再比对「逐字完全相同」：重排本来就是换顺序、去序号。
            # 文字保全是 recompose.validate 的职责（每条要点必须原样出现、不许重复标题），
            # 几何退步由上面的 lint 兜底。
            report.attempts = previous.attempts
            report.repairs = previous.repairs
            slides[index] = candidate
            by_index[index] = item
            reports[index] = report
            info["skill_pages"].append({"index": index, "why": why})
            accepted = True
            logger.info("第 %d 页由技能重排：%s", index + 1, why)
        if not accepted:
            return untouched
        return slides, by_index, reports, info

    # ------------------------------------------------ 美感润色

    def _brand_sourced(self) -> bool:
        """配色是否来自用户上传的模板。是的话「AI 配色」这类判断不成立。"""
        return str(self.preset.get("key", "")).startswith("template")

    def _refine(self, slides, by_index, reports, work: Path, hook: PhaseHook):
        """检测 → 模型只改 CSS → 重实测重体检 → 接受或原样退回。

        返回新的 (slides, by_index, reports, 说明)。说明进 fidelity，用户能看到
        「检出了什么、改了什么、为什么没改」，而不是一个无声的 0。
        """
        if not get_settings().ppt_polish or not self.workspace_id:
            return slides, by_index, reports, _empty_aesthetic()
        try:
            return self._refine_impl(slides, by_index, reports, work, hook)
        except Exception as exc:  # noqa: BLE001 - 润色失败绝不能拖垮整份稿子
            logger.warning("美感润色异常，保留初稿：%s", exc)
            info = _empty_aesthetic()
            info["notes"].append(f"美感润色异常，已保留初稿：{exc}")
            return slides, by_index, reports, info

    def _refine_impl(self, slides, by_index, reports, work: Path, hook: PhaseHook):
        info = _empty_aesthetic()
        untouched = (slides, by_index, reports, info)

        detected = impeccable.inspect(
            slides, work / "aesthetic-before", canvas=self._canvas,
            brand_sourced=self._brand_sourced(),
        )
        info["before"] = detected.summary
        info["findings"] = detected.payload()
        if detected.skipped:
            info["notes"].append(f"美感检测未运行：{detected.skipped}")
            return untouched
        if not detected.findings:
            return untouched

        hook("polishing", 77, f"润色 {len(detected.findings)} 处画面细节")
        patch = polish.request(self.tokens, slides, detected.findings, self.workspace_id)
        info["applied"] = {
            "global": bool(patch.global_css),
            "pages": sorted(patch.pages),
            "deferred": patch.deferred,
            "rejected": patch.rejected,
        }
        info["skill"] = patch.skill
        if patch.empty:
            # 不是失败：检测指出的都是构图层的问题，本轮只改 CSS，改不了就如实留着
            return untouched

        accepted = self._try_patch(
            slides, by_index, reports, patch, work / "polish-1", len(detected.findings)
        )
        if accepted is None and patch.global_css:
            # 全篇补丁把某一页弄坏了：退回逐页补丁再试一次，而不是整份放弃
            patch.global_css = ""
            info["applied"]["global"] = False
            accepted = self._try_patch(
                slides, by_index, reports, patch, work / "polish-2", len(detected.findings)
            )
            if accepted is not None:
                info["notes"].append("全篇补丁未通过复核，已回退为逐页修正")
        if accepted is None:
            info["notes"].append("润色补丁未通过复核（排版或内容退步），已保留初稿")
            return untouched

        new_slides, new_by_index, new_reports, after = accepted
        info["after"] = after.summary
        info["findings"] = after.payload()
        logger.info("美感润色生效：%d 条发现 → %d 条",
                    info["before"]["total"], info["after"]["total"])
        return new_slides, new_by_index, new_reports, info

    def _try_patch(self, slides, by_index, reports, patch, work: Path, budget: int):
        """应用补丁并复核。任何一项退步都返回 None（调用方保留初稿）。

        这里不许把异常放出去：调用链上层把它们当成「实测排版失败」，会把整份稿子
        降级成手写坐标渲染——为了一次润色丢掉全部实测排版，代价完全不成比例。
        """
        try:
            return self._verify_patch(slides, by_index, reports, patch, work, budget)
        except Exception as exc:  # noqa: BLE001 - 润色是加分项
            logger.warning("润色复核失败，保留初稿：%s", exc)
            return None

    def _verify_patch(self, slides, by_index, reports, patch, work: Path, budget: int):
        candidates = polish.apply(slides, patch)
        if all(a.html == b.html for a, b in zip(candidates, slides)):
            return None
        measured = measure.measure(candidates, work, canvas=self._canvas)
        fresh = {int(item["index"]): item for item in measured}
        new_reports = {
            index: lint.inspect(item, self.tokens, layout=candidates[index].kind)
            for index, item in fresh.items()
        }
        for index, report in new_reports.items():
            previous = reports.get(index)
            if previous is None:
                continue
            if len(report.errors) > len(previous.errors):
                logger.info("润色回退：第 %d 页新增 %d 处硬伤", index + 1,
                            len(report.errors) - len(previous.errors))
                return None
            # 保留修复记录，结果卡的说明不该因为一次润色变空
            report.attempts = previous.attempts
            report.repairs = previous.repairs
        for index, item in fresh.items():
            previous = by_index.get(index)
            if previous and _text_of(item) != _text_of(previous):
                logger.info("润色回退：第 %d 页文字被改动", index + 1)
                return None
        after = impeccable.inspect(
            candidates, work / "aesthetic", canvas=self._canvas,
            brand_sourced=self._brand_sourced(),
        )
        if after.summary["total"] > budget:
            logger.info("润色回退：检测发现 %d → %d 条", budget, after.summary["total"])
            return None
        return candidates, fresh, new_reports, after

    # ------------------------------------------------ 设计台预览

    def preview(self, outline: Outline, *, limit: int = PREVIEW_PAGES) -> dict:
        """只出缩略图与体检，不写 pptx。设计台每拧一次旋钮调一次。"""
        plan = compose.plan_deck(outline, self.tokens)
        slides = compose.compose_deck(outline, self.tokens, plan)
        picked = _sample_indices(len(slides), max(1, limit))
        subset = [slides[i] for i in picked]
        with tempfile.TemporaryDirectory(prefix="ppt-preview-") as tmp:
            work = Path(tmp)
            measured = measure.measure(subset, work, canvas=self._canvas)
            reports = [
                lint.inspect(item, self.tokens, layout=slides[int(item["index"])].kind)
                for item in measured
            ]
            # 预览不落盘、看完即弃，直接给原尺寸：灯箱要能读清 16px 的正文
            thumbs = _thumbnails(measured, width=self.tokens["canvas_w"])
        return {
            "slides": [
                {
                    "index": int(item["index"]),
                    "title": slides[int(item["index"])].title,
                    "layout": slides[int(item["index"])].kind,
                    "chapter": slides[int(item["index"])].chapter,
                    "jpeg": thumbs[position],
                    "report": reports[position].payload(),
                }
                for position, item in enumerate(measured)
            ],
            "total_slides": len(slides),
            "layouts_used": [s.kind for s in slides],
            "lint": lint.summarize(reports),
            "tokens": {
                "key": self.tokens["key"],
                "mode": self.tokens["mode"],
                "colors": self.tokens["colors"],
                "chart": self.tokens["chart"],
                "knobs": self.knobs.payload(),
            },
        }


def _empty_aesthetic() -> dict:
    return {
        "before": {"total": 0, "errors": 0, "warnings": 0, "by_rule": {}, "skipped": ""},
        "after": None,
        "applied": {"global": False, "pages": [], "deferred": [], "rejected": []},
        "skill": {},
        "findings": [],
        "notes": [],
    }


def _text_of(item: dict) -> str:
    """一页里所有文字节点的拼接（去空白）。只用来判断「有没有把字弄丢」。"""
    parts: list[str] = []
    for node in item.get("nodes") or []:
        if node.get("kind") != "text":
            continue
        for line in node.get("lines") or []:
            for run in line.get("runs") or []:
                parts.append(str(run.get("text") or ""))
    return re.sub(r"\s+", "", "".join(parts))


def _thumbnails(measured: list[dict], *, width: int = 960) -> list[bytes]:
    out: list[bytes] = []
    for item in measured:
        shot = item.get("screenshot") or ""
        path = Path(shot) if shot else None
        if path and path.is_file():
            try:
                out.append(measure.thumbnail(path, width=width))
                continue
            except OSError:
                pass
        out.append(b"")
    return out


def _fidelity(summary: dict, degradations: list[str], preset: dict,
              aesthetic: dict | None = None) -> dict:
    """体检结论 → 分数与中文摘要。分数只扣硬伤，软告警最多扣 6 分。"""
    score = 100 - min(45, summary["errors"] * 9) - min(6, summary["warnings"])
    score = max(40, score)
    parts = [f"浏览器实测排版，{summary['pages']} 页原生可编辑形状"]
    if summary["repairs"]:
        parts.append(f"{summary['repairs']} 处自动修复")
    if summary["clean_pages"] == summary["pages"]:
        parts.append("逐页体检全部通过")
    else:
        parts.append(f"{summary['pages'] - summary['clean_pages']} 页仍有告警")
    if aesthetic:
        before = (aesthetic.get("before") or {}).get("total") or 0
        after = (aesthetic.get("after") or {}).get("total")
        touched = len((aesthetic.get("applied") or {}).get("pages") or [])
        if after is not None and after < before:
            parts.append(f"美感检测 {before} → {after} 条")
        elif after is not None and touched:
            # 条数没降不代表没做事：剩下的多半是要动结构的构图层问题
            parts.append(f"美感检测 {before} 条，已润色 {touched} 页")
        elif before:
            parts.append(f"美感检测 {before} 条待精修")
    label = "上传模板" if str(preset.get("key", "")).startswith("template") else "内置风格"
    direction = preset.get("direction") or {}
    head = f"{label}「{preset.get('name', '默认')}」"
    if direction.get("name"):
        head = f"方向「{direction['name']}」"
    return {
        "score": score,
        "issues": [],
        "degradations": degradations,
        "direction": direction,
        "summary_zh": f"{head}；" + "；".join(parts),
    }


__all__ = ["HtmlRenderer", "available", "unavailable_reason", "PREVIEW_PAGES"]