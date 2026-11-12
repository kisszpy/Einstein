"""生成器：确认卡 → 套母版渲染 → 落盘 deck/ → 签票 → 结果卡载荷。

同步阻塞（python-pptx 渲染 + 文件落盘），调用方（路由的 SSE 生产者任务）
必须用 `asyncio.to_thread` 包一层，别占事件循环。

拍板结论的落点：
- #2 同名不覆盖：目标文件存在时追加 -2/-3（绝不覆盖用户已有成果）；
- #5 模板源 pptx 是渲染基底：theme_pack_id 缺源文件按不可恢复错误报；
- #4 口径：结果卡 total_slides 以渲染结果为准（内容页 + 附录页）；
- #6 票据：生成完成即签，download_url 带 ticket（iframe 内 fetch 带不了
  Authorization）；票据 15 分钟过期，`GET /generations` 每次重签兜底。
"""

from __future__ import annotations

from typing import Callable

from app.core.logging import get_logger
from app.services import projects as project_registry, tickets
from app.services.capabilities import sandbox
from app.services.ppt_studio import designs, session_store, theme_pack, thumbs
from app.services.ppt_studio.design_renderer import DesignRenderer
from app.services.ppt_studio.htmlkit import measure
from app.services.ppt_studio.htmlkit import renderer as html_renderer
from app.services.ppt_studio.htmlkit import tokens as tk
from app.services.ppt_studio.outline import Outline
from app.services.ppt_studio.renderer import DeckRenderer

logger = get_logger(__name__)

PhaseHook = Callable[[str, int, str], None]


class GenerationError(Exception):
    """可预期失败：retryable=False 表示输入有问题（改了才有意义），True 表示可重试。"""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def _merge_fidelity(renderer_fidelity: dict, pack: dict | None, engine: str = "shapes") -> dict:
    """保真度报告 = 风格适配说明 + 渲染降级（逐页兜底记录）。

    解析期的降级（「N 个版式无内容占位符，将用绝对定位补文本框」）只在真的套母版时
    才算降级。风格迁移是照着模板的美术方向重绘，占位符有没有跟成稿无关——搬过来既是
    噪声，还会让摘要自相矛盾（「11 个版式完整解析，无降级项」下面挂着两条降级）。
    """
    fidelity = dict(renderer_fidelity or {})
    if not pack:
        return fidelity
    pack_fidelity = pack.get("fidelity") or {}
    report = (pack.get("style") or {}).get("report") or {}
    master_fill = engine == "template-shapes"
    sources = list(report.get("notes") or []) + list(fidelity.get("degradations") or [])
    if master_fill:
        sources = list(pack_fidelity.get("degradations") or []) + sources
    merged: list[str] = []
    for note in sources:
        if note not in merged:  # 老包补抽时同一句适配说明会从两条路径各来一次
            merged.append(note)
    fidelity["degradations"] = merged
    fidelity["issues"] = list(
        (pack_fidelity if master_fill else fidelity).get("issues") or []
    )
    fidelity["score"] = max(40, min(int(pack_fidelity.get("score", 100)), int(fidelity.get("score", 100))))
    # 成稿出处随生成记录一起存：模板日后被删，历史结果卡仍要说清当时按谁的风格画的
    name = pack.get("name") or "上传模板"
    fidelity["template"] = {
        "name": name,
        "ratio": report.get("ratio") or "16:9",
        "mode": report.get("mode") or "light",
    }
    # 重绘的摘要要说渲染结果（实测排版 / 逐页体检），不能拿母版解析结论顶替
    first, second = (
        (fidelity, pack_fidelity) if engine == "template" else (pack_fidelity, fidelity)
    )
    fidelity["summary_zh"] = first.get("summary_zh") or second.get("summary_zh", "")
    return fidelity


def _note_fallback(result: dict, note: str) -> dict:
    """回落必须留痕：用户拿到的是「同一份内容、稍逊的排版」，不是无声降级。"""
    if not note:
        return result
    fidelity = dict(result.get("fidelity") or {})
    fidelity["degradations"] = [note] + list(fidelity.get("degradations") or [])
    fidelity["score"] = max(40, int(fidelity.get("score", 100)) - 10)
    result["fidelity"] = fidelity
    result.setdefault("degradations", []).insert(0, note)
    return result


def _render_builtin(preset: dict, outline: Outline, hook: PhaseHook, ws: str = "") -> dict:
    """内置风格：优先 HTML 实测渲染，浏览器不可用/渲染失败回落手写坐标渲染器。"""
    fallback_note = ""
    if html_renderer.available():
        try:
            knobs = tk.Knobs.from_style(outline.style)
            return html_renderer.HtmlRenderer(preset, knobs, ws).render(None, outline, on_phase=hook)
        except measure.MeasureError as exc:
            fallback_note = f"浏览器实测排版不可用（{exc}），已回落基础排版"
            logger.warning("HTML 渲染回落 session outline=%s：%s", outline.title, exc)
        except Exception:  # noqa: BLE001 — 渲染层任何意外都不该让整次生成失败
            fallback_note = "实测排版异常，已回落基础排版"
            logger.exception("HTML 渲染异常，回落 DesignRenderer")
    else:
        fallback_note = f"未启用浏览器实测排版（{html_renderer.unavailable_reason()}），已用基础排版"
    hook("rendering", 60, "基础排版渲染")
    result = DesignRenderer(preset, density=outline.style.density).render(None, outline)
    result["engine"] = "shapes"
    result.setdefault("thumbnails", [])
    return _note_fallback(result, fallback_note)


def _render_template(pack: dict, template_bytes: bytes, outline: Outline, hook: PhaseHook,
                     ws: str = "") -> dict:
    """上传模板：量出它的美术方向后交给 HTML 引擎重绘，而不是往母版占位符里填空。

    浏览器不可用时回落母版套版——那条路虽然版面旧，但比例与配色是模板原样的，
    比回落 16:9 的 DesignRenderer 更贴近用户「按我的模板来」的诉求。
    """
    style = theme_pack.style_of(pack)
    report = style.get("report") or {}
    mode_zh = "深色" if report.get("mode") == "dark" else "浅色"
    hook("rendering", 55, f"按模板风格重绘（{mode_zh} · {report.get('ratio', '16:9')}）")
    fallback_note = ""
    if html_renderer.available():
        try:
            knobs = tk.Knobs.from_style(outline.style)
            result = html_renderer.HtmlRenderer(style["preset"], knobs, ws).render(None, outline, on_phase=hook)
            result["engine"] = "template"
            return result
        except measure.MeasureError as exc:
            fallback_note = f"浏览器实测排版不可用（{exc}），已回落模板母版套版"
            logger.warning("模板 HTML 渲染回落 pack=%s：%s", pack.get("id"), exc)
        except Exception:  # noqa: BLE001 — 渲染层任何意外都不该让整次生成失败
            fallback_note = "实测排版异常，已回落模板母版套版"
            logger.exception("模板 HTML 渲染异常，回落 DeckRenderer")
    else:
        fallback_note = f"未启用浏览器实测排版（{html_renderer.unavailable_reason()}），已回落模板母版套版"
    hook("rendering", 60, "模板母版套版")
    result = DeckRenderer(pack.get("palette") or {}, pack.get("fonts") or {}).render(template_bytes, outline)
    result["engine"] = "template-shapes"
    result.setdefault("thumbnails", [])
    return _note_fallback(result, fallback_note)


def generate_deck(
    session: dict,
    outline: Outline,
    project: str,
    ws: str,
    uid: str,
    filename: str = "",
    deepen_stats: dict | None = None,
    on_phase: PhaseHook | None = None,
) -> dict:
    """生成一份 .pptx 并落盘项目 deck 目录，返回 generation 载荷（含带票的 deck）。

    抛 `GenerationError`（可预期，路由转 error 帧）；
    `sandbox.SandboxError` 由 `projects.require` 抛出（项目不存在 → 404）。
    """
    root = project_registry.require(ws, project, uid)  # SandboxError → 404
    hook: PhaseHook = on_phase or (lambda *_: None)

    template_bytes = b""
    pack = None
    # 内置风格（builtin:xxx）走设计渲染器，不需要模板文件；其余 id 一律按主题包处理
    preset = designs.resolve_id(outline.style.theme_pack_id)
    if designs.wants_auto(outline.style.theme_pack_id, outline.style.preset_theme):
        # 没选风格：按内容现配一套方向，而不是落回某个固定家族
        preset = theme_pack.preset_for(
            outline.style.theme_pack_id, outline.style.preset_theme, ws,
            outline=outline, session_id=session["id"],
        )
    elif outline.style.theme_pack_id and preset is None:
        pack = theme_pack.get_visible_pack(outline.style.theme_pack_id, ws)
        if pack is None:
            raise GenerationError("所选模板不可见或已删除（theme_pack_not_found）", retryable=False)
        template_bytes = theme_pack.source_bytes(pack)
        if not template_bytes:
            raise GenerationError("模板源文件已丢失，请重新上传模板（template_source_missing）", retryable=False)

    if template_bytes:
        result = _render_template(pack, template_bytes, outline, hook, ws)
    else:
        preset = preset or designs.resolve_theme(outline.style.preset_theme) or designs.default_preset()
        result = _render_builtin(preset, outline, hook, ws)

    # ---- 落盘：deck/<安全文件名>.pptx，同名追加 -2/-3 绝不覆盖 ----
    blob = result["blob"]
    deck_dir = root / "deck"
    deck_dir.mkdir(parents=True, exist_ok=True)
    base = theme_pack.safe_filename(filename or outline.title or "presentation")
    target = deck_dir / f"{base}.pptx"
    suffix = 2
    while target.exists():
        target = deck_dir / f"{base}-{suffix}.pptx"
        suffix += 1
    if sandbox.dir_size(root) + len(blob) > sandbox.MAX_PROJECT_BYTES:
        raise GenerationError(
            f"项目总体积超过 {sandbox.human_size(sandbox.MAX_PROJECT_BYTES)} 上限（quota_exceeded），"
            "请清理旧产物后重试",
            retryable=True,
        )
    target.write_bytes(blob)
    rel = f"deck/{target.name}"

    # ---- 签票 + 落生成记录 + 会话收尾 ----
    ticket = tickets.issue(uid, ws)
    engine = result.get("engine", "shapes")
    fidelity = _merge_fidelity(result["fidelity"], pack, engine)
    # 深化层统计进保真度报告：回落页让用户知道哪些页沿用了确认卡原文
    stats = deepen_stats or {}
    failed = [f for f in stats.get("failed") or [] if f]
    if failed:
        sample = "、".join(failed[:3]) + ("…" if len(failed) > 3 else "")
        fidelity["degradations"] = list(fidelity.get("degradations") or []) + [
            f"{len(failed)} 页要点深化失败，沿用确认卡原文：{sample}"
        ]
        fidelity["score"] = max(40, int(fidelity.get("score", 100)) - 5)
    elif stats.get("deepened"):
        fidelity["summary_zh"] = (
            f"{fidelity.get('summary_zh', '')}（{stats['deepened']} 页要点已深化）".strip("；; ")
        )
    fidelity["engine"] = engine
    generation = session_store.insert_generation(
        session_id=session["id"],
        project=project,
        deck_path=rel,
        total_slides=int(result["total_slides"]),
        layouts_used=list(result["layouts_used"]),
        fidelity=fidelity,
    )
    # 缩略图按生成 id 落服务端缓存：结果卡与历史抽屉都靠它「一眼看到成品」
    saved_thumbs = thumbs.save(generation["id"], result.get("thumbnails") or [])
    if saved_thumbs:
        logger.info("结果缩略图 %d 张 → %s", saved_thumbs, generation["id"])
    session_store.update_status(
        session["id"], "done",
        page_count=outline.content_page_count(),
        total_slides=int(result["total_slides"]),
    )
    deck = {
        "project": project,
        "path": rel,
        "name": target.name,
        "bytes": len(blob),
        "ticket": ticket["ticket"],
        "download_url": f"/api/projects/{project}/download?path={rel}&ticket={ticket['ticket']}",
    }
    logger.info("PPT 生成完成：%s（%d 页，%.0fKB）", rel, result["total_slides"], len(blob) / 1024)
    return session_store.generation_payload(generation, deck=deck)
