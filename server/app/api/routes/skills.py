"""技能管理路由：本地编辑、GitHub 安装、技能市场、版本管理、zip 互通、试运行。"""

import urllib.parse

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import current_uid, current_user, current_workspace
from app.services import llm, skill_installer, skill_market
from app.services.skills import Skill, get_skill_store

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = []
    visibility: str = "private"


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    enabled: bool | None = None
    visibility: str | None = None


VISIBILITIES = ("private", "workspace", "shared")


class InstallRequest(BaseModel):
    url: str
    ref: str = ""
    path: str = ""


class PreviewRequest(BaseModel):
    url: str
    ref: str = ""
    path: str = ""


def _dump(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "system_prompt": skill.system_prompt,
        "tools": skill.tools,
        "builtin": skill.builtin,
        "enabled": skill.enabled,
        "workspace_id": skill.workspace_id,
        "source": skill.source,
        "source_url": skill.source_url,
        "repo": skill.repo,
        "ref": skill.ref,
        "commit": skill.commit,
        "subpath": skill.subpath,
        "author": skill.author,
        "license": skill.license,
        "files": skill.files,
        "installed_at": skill.installed_at,
        "updated_at": skill.updated_at,
        "version": skill.version,
        "versions": skill.versions,
        "visibility": skill.visibility,
        "owner_id": skill.owner_id,
    }


def _require_read(skill: Skill | None, ws: str, uid: str) -> Skill:
    """可读：内置技能、预置安装（'' ）与自己在当前空间装的技能。"""
    store = get_skill_store()
    if skill is None or store.visible(skill.id, ws, uid) is None:
        raise HTTPException(404, "技能不存在")
    return skill


def _require_edit(skill: Skill | None, ws: str, uid: str, is_admin: bool) -> Skill:
    """可改：自己装的技能；预置技能只有管理员能改（别人改会改到所有人）。"""
    _require_read(skill, ws, uid)
    assert skill is not None
    if not get_skill_store().can_edit(skill, uid, is_admin):
        raise HTTPException(403, "预置技能只有管理员可以修改")
    return skill


def _raise_install_error(exc: Exception) -> None:
    message = str(exc)
    if "限流" in message or "额度" in message:
        raise HTTPException(429, message) from exc
    raise HTTPException(400, message) from exc


# ---------- 市场与安装（静态路径需先于 /{skill_id} 声明） ----------


@router.get("/market")
async def market(
    q: str = "",
    source: str = "github",
    limit: int = Query(default=20, ge=1, le=50),
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    try:
        result = skill_market.browse(source=source, query=q, limit=limit)
    except Exception as exc:  # noqa: BLE001 — 统一转成可读错误
        _raise_install_error(exc)

    installed_by_repo: dict[str, int] = {}
    for skill in get_skill_store().list_skills(ws, uid):
        if skill.repo:
            installed_by_repo[skill.repo] = installed_by_repo.get(skill.repo, 0) + 1

    items = [
        {**item, "installed": installed_by_repo.get(item.get("repo", ""), 0)}
        for item in result.get("items", [])
    ]
    return {
        "source": result.get("source", source),
        "query": q,
        "total": result.get("total", len(items)),
        "cached": result.get("cached", False),
        "rate_limited": result.get("rate_limited", False),
        "note": result.get("note", ""),
        "sources": skill_market.known_sources(),
        "errors": result.get("errors", []),
        "items": items,
    }


@router.post("/install/preview")
async def preview_install(req: PreviewRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    try:
        return skill_installer.preview(req.url, req.ref, req.path)
    except skill_installer.SkillInstallError as exc:
        _raise_install_error(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"预览失败：{exc}") from exc


@router.post("/install")
async def install_skill(req: InstallRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    store = get_skill_store()
    try:
        skill, created = skill_installer.install(store, ws, req.url, req.ref, req.path, uid)
    except skill_installer.SkillInstallError as exc:
        _raise_install_error(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"安装失败：{exc}") from exc
    return {"skill": _dump(skill), "created": created}


# ---------- 技能增删改查 ----------


@router.get("")
async def list_skills(ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> list[dict]:
    return [_dump(s) for s in get_skill_store().list_skills(ws, uid)]


@router.post("")
async def create_skill(req: SkillCreate, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "技能名称不能为空")
    if req.visibility not in VISIBILITIES:
        raise HTTPException(400, f"visibility 只能是 {' / '.join(VISIBILITIES)}")
    store = get_skill_store()
    skill = store.add(
        req.name.strip(), req.description, req.system_prompt, req.tools,
        workspace_id=ws, owner_id=uid, visibility=req.visibility,
    )
    store.save()
    return _dump(skill)


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str, req: SkillUpdate, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    store = get_skill_store()
    _require_edit(store.get(skill_id), ws, uid, user.is_admin)
    if req.visibility is not None and req.visibility not in VISIBILITIES:
        raise HTTPException(400, f"visibility 只能是 {' / '.join(VISIBILITIES)}")
    skill = store.update(
        skill_id,
        name=req.name.strip() if req.name else None,
        description=req.description,
        system_prompt=req.system_prompt,
        tools=req.tools,
        enabled=req.enabled,
        visibility=req.visibility,
    )
    if skill is None:
        raise HTTPException(404, "技能不存在")
    if any(v is not None for v in (req.name, req.description, req.system_prompt, req.tools)):
        # 内容变更固化为新版本，可随时回滚
        store.snapshot(skill_id, note="编辑")
    store.save()
    return _dump(skill)


# ---------- 版本管理 / 导入导出 / 试运行 ----------


@router.get("/{skill_id}/versions")
async def list_versions(skill_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    skill = _require_read(get_skill_store().get(skill_id), ws, uid)
    return {"version": skill.version, "versions": skill.versions}


class RollbackRequest(BaseModel):
    version: str


@router.post("/{skill_id}/rollback")
async def rollback_skill(
    skill_id: str, req: RollbackRequest, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    store = get_skill_store()
    _require_edit(store.get(skill_id), ws, uid, user.is_admin)
    skill = store.rollback(skill_id, req.version)
    if skill is None:
        raise HTTPException(404, f"版本 {req.version} 不存在")
    store.save()
    return _dump(skill)


@router.get("/{skill_id}/export")
async def export_skill(skill_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> Response:
    skill = _require_read(get_skill_store().get(skill_id), ws, uid)
    data = skill_installer.export_zip(skill)
    # HTTP header 只允许 latin-1：文件名剥离非 ASCII，中文名走 RFC 5987 filename*
    ascii_safe = "".join(
        ch for ch in skill.name if ch.isascii() and (ch.isalnum() or ch in "-_")
    ) or "skill"
    quoted = urllib.parse.quote(f"{skill.name}-{skill.version}.zip")
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_safe}-{skill.version}.zip"; '
                f"filename*=UTF-8''{quoted}"
            )
        },
    )


@router.post("/import")
async def import_skill(
    file: UploadFile = File(...), ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    data = await file.read()
    if len(data) > skill_installer.MAX_IMPORT_BYTES:
        raise HTTPException(400, "zip 文件超过 50MB 上限")
    try:
        skill = skill_installer.import_zip(data, get_skill_store(), ws, uid)
    except skill_installer.SkillInstallError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _dump(skill)


class TryRequest(BaseModel):
    message: str
    temperature: float = 0.7


@router.post("/{skill_id}/try")
async def try_skill(skill_id: str, req: TryRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    """沙箱试运行：用技能的系统提示词回答一条样例消息（限长，不落历史）。"""
    skill = _require_read(get_skill_store().get(skill_id), ws, uid)
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "试运行消息不能为空")
    system = skill.system_prompt or "你是通用助手。"
    system = system[:4000]
    import time as _time

    from langchain_core.messages import HumanMessage, SystemMessage

    t0 = _time.perf_counter()
    try:
        reply, usage = await llm.complete(
            [SystemMessage(content=system), HumanMessage(content=message[:2000])],
            temperature=req.temperature,
            max_tokens=400,
            kind="skill_try",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"试运行失败：{type(exc).__name__}: {exc}") from exc
    elapsed_ms = round((_time.perf_counter() - t0) * 1000)
    return {
        "reply": reply,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "version": skill.version,
    }


@router.post("/{skill_id}/update")
async def update_from_source(
    skill_id: str, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    store = get_skill_store()
    skill = _require_edit(store.get(skill_id), ws, uid, user.is_admin)
    if not skill.repo:
        raise HTTPException(400, "该技能不是从 GitHub 安装的，无法检查更新")

    try:
        updated, _ = skill_installer.install(
            store, ws, f"https://github.com/{skill.repo}", skill.ref, skill.subpath,
            skill.owner_id,
        )
    except skill_installer.SkillInstallError as exc:
        _raise_install_error(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"更新失败：{exc}") from exc

    changed = updated.commit != skill.commit
    return {"skill": _dump(updated), "changed": changed, "previous_commit": skill.commit}


@router.get("/{skill_id}/files")
async def list_files(skill_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> dict:
    skill = _require_read(get_skill_store().get(skill_id), ws, uid)
    return {"files": skill.files, "source_url": skill.source_url}


@router.get("/{skill_id}/file")
async def read_file(
    skill_id: str, path: str = Query(...), ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    _require_read(get_skill_store().get(skill_id), ws, uid)
    try:
        content, is_text = skill_installer.read_text_file(skill_id, path)
    except skill_installer.SkillInstallError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"path": path, "content": content, "is_text": is_text}


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str, ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid), user=Depends(current_user),
) -> dict:
    store = get_skill_store()
    _require_edit(store.get(skill_id), ws, uid, user.is_admin)
    if not store.delete(skill_id):
        raise HTTPException(400, "技能不存在或为内置技能（不可删除）")
    skill_installer.remove_files(skill_id)
    store.save()
    return {"ok": True}
