"""AI PPT 制作路由：主题包管理 + 会话/消息 + 取材出卡 + 生成 + 结果卡。

SSE 骨架照 routes/chat.py：生产者任务 + asyncio.Queue + 15 秒注释心跳，
取材（联网检索可能十几秒）与渲染（大模板几秒）都不会被空闲超时掐断。

鉴权依赖全站中间件（Bearer 会话）；DELETE /templates 需要判 admin，
用 current_user 拿完整 User。
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.api.deps import current_uid, current_user, current_workspace
from app.core.logging import get_logger
from app.services import projects as project_registry
from app.services.capabilities import sandbox
from app.services.experts import get_expert_store
from app.services.ppt_studio import designs, generator, outliner, session_store, theme_pack, thumbs
from app.services.ppt_studio.deepen import deepen_outline
from app.services.ppt_studio.htmlkit import measure
from app.services.ppt_studio.htmlkit import renderer as html_renderer
from app.services.ppt_studio.htmlkit import tokens as tk
from app.services.ppt_studio.outline import Outline, OutlineValidationError
from app.services.users import User

logger = get_logger(__name__)

router = APIRouter(prefix="/ppt", tags=["ppt"])

PARSE_TIMEOUT_SEC = 20
PREVIEW_TIMEOUT_SEC = 60
ALLOWED_TEMPLATE_SUFFIX = ".pptx"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(events: AsyncIterator[tuple[str, dict]]) -> AsyncIterator[str]:
    """(event, data) 序列 → SSE 文本流。生产者在任务里跑，消费端 15s 心跳。"""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    stop = object()

    async def _pump() -> None:
        try:
            async for event, data in events:
                await queue.put((event, data))
        except Exception as exc:  # noqa: BLE001 — 异常也走队列，保持事件顺序
            await queue.put(exc)
        finally:
            await queue.put(stop)

    pump = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is stop:
                break
            if isinstance(item, Exception):
                yield _sse("error", {"message": f"{type(item).__name__}: {item}", "retryable": True})
                break
            event, data = item
            yield _sse(event, data)
    finally:
        pump.cancel()
        try:
            await pump
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


# ================================================================ 主题包


@router.post("/templates")
async def upload_template(
    file: UploadFile,
    name: str = "",
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """上传 .pptx 模板 → 解析为主题包（同步等待解析完成，前端显示「解析中→已就绪」）。"""
    filename = file.filename or "template.pptx"
    if not filename.lower().endswith(ALLOWED_TEMPLATE_SUFFIX):
        raise HTTPException(400, "只支持 .pptx 模板文件（template_invalid_suffix）")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空（template_empty）")
    if len(raw) > theme_pack.MAX_TEMPLATE_BYTES:
        raise HTTPException(
            400,
            f"模板超过 {sandbox.human_size(theme_pack.MAX_TEMPLATE_BYTES)} 上限（template_too_large）",
        )
    try:
        pack = await asyncio.wait_for(
            asyncio.to_thread(
                theme_pack.create_pack,
                workspace_id=ws, owner_id=uid, raw=raw, filename=filename, name=name,
            ),
            timeout=PARSE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            400, f"模板解析超时（>{PARSE_TIMEOUT_SEC}s），请精简模板后重试（template_parse_timeout）"
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, f"{exc}（template_invalid）") from exc
    except Exception as exc:  # noqa: BLE001 — python-pptx 对损坏包抛的异常类型很杂
        logger.warning("模板解析异常 %s %s：%s", type(exc).__name__, filename, exc)
        raise HTTPException(400, "无法解析演示文稿（template_invalid）") from exc
    return theme_pack.list_payload(pack)


@router.get("/templates")
def list_templates(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> list[dict]:
    """workspace 级共享：同空间全员可见（拍板结论 #1）。

    返回 = 内置设计风格（kind=builtin，无需上传、可直接生成）+ 本空间上传的模板包。
    前端用同一个卡片模型渲染，内置项排前面。
    """
    return designs.list_payload() + [theme_pack.list_payload(p) for p in theme_pack.list_packs(ws, uid)]


@router.get("/templates/{pack_id}")
def get_template(
    pack_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    pack = theme_pack.get_visible_pack(pack_id, ws)
    if pack is None:
        raise HTTPException(404, "模板不存在或已删除")
    return theme_pack.detail_payload(pack)


@router.delete("/templates/{pack_id}")
def delete_template(
    pack_id: str,
    user: User = Depends(current_user),
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    pack = theme_pack.get_visible_pack(pack_id, ws)
    if pack is None:
        raise HTTPException(404, "模板不存在或已删除")
    if not theme_pack.can_edit(pack, uid, user.is_admin):
        raise HTTPException(403, "只有模板上传者或管理员可以删除共享模板")
    if not theme_pack.delete_pack(pack_id):
        raise HTTPException(404, "模板不存在或已删除")
    return {"ok": True}


# ================================================================ 会话


class CreateSessionRequest(BaseModel):
    title: str = ""
    project: str = ""
    expert_id: str = "ppt-beautifier"
    skill_ids: list[str] = []
    kb_ids: list[str] = []
    theme_pack_id: str = ""


@router.get("/sessions")
def list_sessions(
    limit: int = 50, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> list[dict]:
    return [
        session_store.session_summary(s) for s in session_store.list_sessions(ws, uid, limit)
    ]


@router.post("/sessions")
def create_session(
    req: CreateSessionRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    if req.project:
        try:
            project_registry.require(ws, req.project, uid)
        except sandbox.SandboxError as exc:
            raise HTTPException(400, f"{exc}（project_not_found）") from exc
    if (
        req.theme_pack_id
        and not designs.is_builtin_id(req.theme_pack_id)
        and theme_pack.get_visible_pack(req.theme_pack_id, ws) is None
    ):
        raise HTTPException(400, "所选模板不可见或已删除（theme_pack_not_found）")
    expert_id = req.expert_id or "ppt-beautifier"
    if expert_id and get_expert_store().visible(expert_id, ws, uid) is None:
        raise HTTPException(400, "所选专家不可见（expert_not_found）")
    session = session_store.create_session(
        workspace_id=ws,
        owner_id=uid,
        title=req.title,
        project=req.project,
        expert_id=expert_id,
        skill_ids=req.skill_ids,
        kb_ids=req.kb_ids,
        theme_pack_id=req.theme_pack_id,
    )
    return session_store.session_summary(session)


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    session = session_store.require_session(session_id, ws, uid)
    if session is None:
        raise HTTPException(404, "会话不存在或已删除")
    return {
        "session": session_store.session_summary(session),
        "messages": [
            {
                "id": m["id"],
                "seq": int(m.get("seq") or 0),
                "role": m.get("role", "user"),
                "content": m.get("content", ""),
                "payload": m.get("payload") or {},
                "created_at": m.get("created_at", ""),
            }
            for m in session_store.list_messages(session_id)
        ],
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    if session_store.require_session(session_id, ws, uid) is None:
        raise HTTPException(404, "会话不存在或已删除")
    _ok, deleted = session_store.delete_session(session_id)
    return {"ok": True, "deleted_messages": deleted}


# ================================================================ 取材出卡（SSE）


class AnalyzeRequest(BaseModel):
    prompt: str
    expert_id: str | None = None
    skill_ids: list[str] | None = None
    kb_ids: list[str] | None = None
    theme_pack_id: str | None = None
    page_target: int = 0
    web_mode: str = "auto"


@router.post("/sessions/{session_id}/analyze")
def analyze(
    session_id: str,
    req: AnalyzeRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> StreamingResponse:
    """取材 + 出卡。事件：meta → phase → web → sources → (delta|error|clarification|outline) → done。"""
    session = session_store.require_session(session_id, ws, uid)
    if session is None:
        raise HTTPException(404, "会话不存在或已删除")
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(400, "需求描述不能为空（prompt_required）")
    if session_store.get_session(session_id).get("status") == "generating":
        raise HTTPException(409, "上一轮还在生成中，请稍候（session_busy）")

    # 输入区三件套可能随本轮更新：非 None 才覆盖会话配置
    patch: dict = {"title": prompt[:20] or session.get("title", "")}
    if req.expert_id is not None and req.expert_id != "":
        if get_expert_store().visible(req.expert_id, ws, uid) is None:
            raise HTTPException(400, "所选专家不可见（expert_not_found）")
        patch["expert_id"] = req.expert_id
    if req.skill_ids is not None:
        patch["skill_ids"] = req.skill_ids
    if req.kb_ids is not None:
        patch["kb_ids"] = req.kb_ids
    if req.theme_pack_id is not None:
        if (
            req.theme_pack_id
            and not designs.is_builtin_id(req.theme_pack_id)
            and theme_pack.get_visible_pack(req.theme_pack_id, ws) is None
        ):
            raise HTTPException(400, "所选模板不可见或已删除（theme_pack_not_found）")
        patch["theme_pack_id"] = req.theme_pack_id
    session_store.update_session(session_id, patch)
    session_store.update_status(session_id, "analyzing")
    fresh = session_store.get_session(session_id)

    history = [
        (m.get("role", "user"), m.get("content", ""))
        for m in session_store.list_messages(session_id)
    ]
    request_payload = {
        "prompt": prompt,
        "expert_id": fresh.get("expert_id", ""),
        "kb_ids": fresh.get("kb_ids") or [],
        "theme_pack_id": fresh.get("theme_pack_id", ""),
        "page_target": req.page_target,
        "web_mode": req.web_mode,
    }
    return StreamingResponse(
        _stream(outliner.analyze(fresh, request_payload, ws, uid, history)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ================================================================ 确认卡保存


class SaveOutlineRequest(BaseModel):
    outline: dict
    message_id: str | None = None


def _build_outline(data: dict, ws: str) -> Outline:
    """dict → 规范化 Outline；主题包可见性并入校验。"""
    try:
        outline = Outline.model_validate(data or {}).normalize()
    except Exception as exc:  # noqa: BLE001 — pydantic 校验错统一转 400
        raise HTTPException(400, f"确认卡结构不合规：{exc}（outline_invalid）") from exc
    pack_visible = True
    if outline.style.theme_pack_id:
        pack_visible = (
            designs.is_builtin_id(outline.style.theme_pack_id)
            or theme_pack.get_visible_pack(outline.style.theme_pack_id, ws) is not None
        )
    try:
        from app.services.ppt_studio.outline import ensure_valid

        ensure_valid(outline, theme_pack_visible=pack_visible)
    except OutlineValidationError as exc:
        raise HTTPException(400, f"{exc}（{exc.code}）") from exc
    return outline


@router.put("/sessions/{session_id}/outline")
def save_outline(
    session_id: str,
    req: SaveOutlineRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """保存用户编辑后的确认卡：更新原消息 payload 或追加一条 assistant 消息。"""
    session = session_store.require_session(session_id, ws, uid)
    if session is None:
        raise HTTPException(404, "会话不存在或已删除")
    outline = _build_outline(req.outline, ws)
    dump = outline.model_dump()
    message_id = req.message_id or ""
    if message_id:
        existing = session_store.message_payload(session_id, message_id)
        if existing is None or "outline" not in existing:
            message_id = ""  # 原卡消息不存在（比如已被清掉）：落一条新消息
        else:
            session_store.update_message_payload(session_id, message_id, {"outline": dump})
    if not message_id:
        message = session_store.append_message(session_id, "assistant", outline.title, {"outline": dump})
        message_id = message["id"]
    session_store.update_status(
        session_id, "awaiting_confirm",
        page_count=outline.content_page_count(),
        total_slides=outline.total_slides(),
    )
    return {
        "outline": dump,
        "page_count": outline.content_page_count(),
        "total_slides": outline.total_slides(),
        "warnings": outline.warnings(),
        "message_id": message_id,
    }


# ================================================================ 生成（SSE）


class GenerateRequest(BaseModel):
    outline: dict | None = None
    project: str | None = None
    filename: str = ""


@router.post("/sessions/{session_id}/generate")
def generate(
    session_id: str,
    req: GenerateRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> StreamingResponse:
    """确认生成。事件：meta → phase(deepening) → phase(rendering) → result → done | error。"""
    session = session_store.require_session(session_id, ws, uid)
    if session is None:
        raise HTTPException(404, "会话不存在或已删除")

    project = (req.project or session.get("project") or "").strip()
    if not project:
        raise HTTPException(
            400, "缺少项目上下文，请先在输入区选择或新建项目（project_required）"
        )
    try:
        project_registry.require(ws, project, uid)
    except sandbox.SandboxError as exc:
        raise HTTPException(404, f"{exc}（project_not_found）") from exc

    # outline：显式回传 > 会话里最近一份已保存卡
    outline_data = req.outline
    if outline_data is None:
        latest = session_store.latest_outline(session_id)
        outline_data = latest[1] if latest else None
    if not outline_data:
        raise HTTPException(400, "没有可生成的确认卡，请先完成取材出卡（outline_missing）")
    outline = _build_outline(outline_data, ws)

    session_store.update_status(session_id, "generating")
    fresh = session_store.get_session(session_id)

    async def _events() -> AsyncIterator[tuple[str, dict]]:
        yield "meta", {"session_id": session_id, "project": project}
        # ---- 深化层：确认卡要点是骨架，逐页扩写成稿（失败回落原文，绝不阻断）----
        yield "phase", {"phase": "deepening", "label": "正在深化每页内容", "progress": 20}
        deepen_stats: dict = {}
        try:
            deepen_stats = await deepen_outline(outline, ws)
        except Exception:  # noqa: BLE001 — 深化层任何异常都不阻断生成
            logger.exception("要点深化异常 session=%s，改用确认卡原文", session_id)
            deepen_stats = {"deepened": 0, "kept": 0, "failed": ["deepen_error"]}
        yield "phase", {"phase": "rendering", "label": "正在排版每一页", "progress": 40}

        # 渲染在别的线程里跑（Chromium + python-pptx 都是同步阻塞），
        # 进度通过线程安全队列回灌到这条 SSE，用户能看到「实测→修复→写形状」。
        loop = asyncio.get_running_loop()
        progress: asyncio.Queue[tuple[str, int, str]] = asyncio.Queue()

        def on_phase(phase: str, percent: int, label: str) -> None:
            loop.call_soon_threadsafe(progress.put_nowait, (phase, percent, label))

        task = asyncio.create_task(
            asyncio.to_thread(
                generator.generate_deck, fresh, outline, project, ws, uid, req.filename,
                deepen_stats, on_phase,
            )
        )
        while not task.done():
            try:
                phase, percent, label = await asyncio.wait_for(progress.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            yield "phase", {"phase": phase, "label": label, "progress": percent}
        while not progress.empty():
            phase, percent, label = progress.get_nowait()
            yield "phase", {"phase": phase, "label": label, "progress": percent}
        try:
            payload = task.result()
        except generator.GenerationError as exc:
            yield "error", {"message": str(exc), "retryable": exc.retryable}
            session_store.update_status(session_id, "failed")
            return
        except Exception as exc:  # noqa: BLE001 — 渲染/落盘意外失败走可重试 error 帧
            logger.exception("PPT 生成意外失败 session=%s", session_id)
            yield "error", {"message": f"生成失败：{type(exc).__name__}（generation_failed）", "retryable": True}
            session_store.update_status(session_id, "failed")
            return
        yield "phase", {"phase": "done", "label": "生成完成", "progress": 100}
        message = session_store.append_message(
            session_id, "assistant", payload["deck"]["name"], {"result": payload}
        )
        yield "result", {"generation": payload}
        yield "done", {"generation_id": payload["generation_id"], "message_id": message["id"]}

    return StreamingResponse(
        _stream(_events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ================================================================ 设计台预览


class PreviewRequest(BaseModel):
    """设计台每次拧旋钮都调它。outline 缺省时取会话里最近一份卡。"""

    outline: dict | None = None
    limit: int = 6


@router.get("/designs")
def list_designs(ws: str = Depends(current_workspace)) -> dict:
    """设计家族目录 + 旋钮取值范围。前端据此画设计台，不写死任何色值。

    上传的公司模板也是一支家族（排最前），所以设计台对模板同样能拧旋钮。
    """
    families: list[dict] = []
    for pack in theme_pack.list_packs(ws, ""):
        try:
            families.append(theme_pack.family_payload(pack))
        except Exception:  # noqa: BLE001 — 单个坏模板不该让整个设计台打不开
            logger.exception("模板家族卡生成失败 pack=%s", pack.get("id"))
    families.extend(tk.all_families())
    return {
        "families": families,
        "knobs": {
            "density": ["loose", "normal", "dense"],
            "type_scale": {"min": 0.88, "max": 1.14, "step": 0.02, "default": 1.0},
            "radius": {"min": 0, "max": 28, "step": 1, "default": 14},
            "whitespace": {"min": 0.8, "max": 1.25, "step": 0.05, "default": 1.0},
            "graphic": ["auto", "flat", "gradient", "outline"],
            "seed": {"min": 0, "max": 9999},
        },
        "engine": {
            "html": html_renderer.available(),
            "reason": html_renderer.unavailable_reason(),
        },
    }


@router.post("/sessions/{session_id}/preview")
async def preview_design(
    session_id: str,
    req: PreviewRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """按当前确认卡 + 旋钮实测若干页，返回 JPEG 缩略图（data URL）与逐页体检。

    只读：不落盘、不签票、不写生成记录。浏览器不可用时返回 available=false，
    前端据此把设计台降级为「色板预览」而不是报错。
    """
    session = session_store.require_session(session_id, ws, uid)
    if session is None:
        raise HTTPException(404, "会话不存在或已删除")
    outline_data = req.outline
    if outline_data is None:
        latest = session_store.latest_outline(session_id)
        outline_data = latest[1] if latest else None
    if not outline_data:
        raise HTTPException(400, "没有可预览的确认卡（outline_missing）")
    outline = _build_outline(outline_data, ws)
    if not html_renderer.available():
        return {"available": False, "reason": html_renderer.unavailable_reason(), "slides": []}

    def _job() -> dict:
        # 方向的解析要放在工作线程里：它可能调一次模型，而模型调用不能在事件循环里跑
        preset = theme_pack.preset_for(
            outline.style.theme_pack_id, outline.style.preset_theme, ws,
            outline=outline, session_id=session_id,
        )
        renderer = html_renderer.HtmlRenderer(preset, tk.Knobs.from_style(outline.style), ws)
        return renderer.preview(outline, limit=max(1, min(12, req.limit)))

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_job),
            timeout=PREVIEW_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, f"预览超时（>{PREVIEW_TIMEOUT_SEC}s），请减少页数后重试") from exc
    except measure.MeasureError as exc:
        return {"available": False, "reason": str(exc), "slides": []}
    for slide in result["slides"]:
        blob = slide.pop("jpeg", b"")
        slide["image"] = f"data:image/jpeg;base64,{base64.b64encode(blob).decode()}" if blob else ""
    return {"available": True, **result}


@router.get("/thumbs/{generation_id}/{index}")
def get_thumb(
    generation_id: str,
    index: int,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> Response:
    """结果卡缩略图。<img> 带不了 Authorization，所以这条也认预览票据。"""
    generation = session_store.get_generation(generation_id)
    if generation is None:
        raise HTTPException(404, "生成记录不存在")
    if session_store.require_session(generation.get("session_id", ""), ws, uid) is None:
        raise HTTPException(404, "生成记录不存在")
    blob = thumbs.load(generation_id, index)
    if blob is None:
        raise HTTPException(404, "缩略图不存在或已过期")
    return Response(
        content=blob, media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=900"},
    )


# ================================================================ 结果卡（票据重签）


@router.get("/sessions/{session_id}/generations")
def list_generations(
    session_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> list[dict]:
    """历次结果卡。票据 15 分钟过期，这里每次调用重新签票（打开抽屉前的兜底）。"""
    if session_store.require_session(session_id, ws, uid) is None:
        raise HTTPException(404, "会话不存在或已删除")
    rows = session_store.list_generations(session_id)
    out: list[dict] = []
    for row in rows:
        deck: dict | None = None
        project = row.get("project", "")
        deck_path = row.get("deck_path", "")
        if project and deck_path:
            try:
                root = project_registry.require(ws, project, uid)
                target = sandbox.resolve_in(root, deck_path)
                if target.is_file():
                    ticket = tickets_issue(uid, ws)
                    deck = {
                        "project": project,
                        "path": deck_path,
                        "name": target.name,
                        "bytes": target.stat().st_size,
                        "ticket": ticket,
                        "download_url": (
                            f"/api/projects/{project}/download?path={deck_path}&ticket={ticket}"
                        ),
                    }
            except sandbox.SandboxError:
                deck = None  # 项目被删：结果卡保留，只是没有可下载的文件
        out.append(session_store.generation_payload(row, deck=deck))
    return out


def tickets_issue(uid: str, ws: str) -> str:
    from app.services import tickets as tickets_service

    return tickets_service.issue(uid, ws)["ticket"]
