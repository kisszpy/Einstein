"""技能管理路由：本地编辑、GitHub 安装、技能市场。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services import skill_installer, skill_market
from app.services.skills import Skill, get_skill_store

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = []


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    enabled: bool | None = None


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
    }


def _require_own(skill: Skill | None, ws: str) -> Skill:
    """内置技能全员可改；自定义技能只能由所属工作区操作。"""
    if skill is None or (not skill.builtin and skill.workspace_id != ws):
        raise HTTPException(404, "技能不存在")
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
    ws: str = Depends(current_workspace),
) -> dict:
    try:
        result = skill_market.browse(source=source, query=q, limit=limit)
    except Exception as exc:  # noqa: BLE001 — 统一转成可读错误
        _raise_install_error(exc)

    installed_by_repo: dict[str, int] = {}
    for skill in get_skill_store().list_skills(ws):
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
async def preview_install(req: PreviewRequest, ws: str = Depends(current_workspace)) -> dict:
    try:
        return skill_installer.preview(req.url, req.ref, req.path)
    except skill_installer.SkillInstallError as exc:
        _raise_install_error(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"预览失败：{exc}") from exc


@router.post("/install")
async def install_skill(req: InstallRequest, ws: str = Depends(current_workspace)) -> dict:
    store = get_skill_store()
    try:
        skill, created = skill_installer.install(store, ws, req.url, req.ref, req.path)
    except skill_installer.SkillInstallError as exc:
        _raise_install_error(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"安装失败：{exc}") from exc
    return {"skill": _dump(skill), "created": created}


# ---------- 技能增删改查 ----------


@router.get("")
async def list_skills(ws: str = Depends(current_workspace)) -> list[dict]:
    return [_dump(s) for s in get_skill_store().list_skills(ws)]


@router.post("")
async def create_skill(req: SkillCreate, ws: str = Depends(current_workspace)) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "技能名称不能为空")
    store = get_skill_store()
    skill = store.add(req.name.strip(), req.description, req.system_prompt, req.tools, workspace_id=ws)
    store.save()
    return _dump(skill)


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str, req: SkillUpdate, ws: str = Depends(current_workspace)
) -> dict:
    store = get_skill_store()
    _require_own(store.get(skill_id), ws)
    skill = store.update(
        skill_id,
        name=req.name.strip() if req.name else None,
        description=req.description,
        system_prompt=req.system_prompt,
        tools=req.tools,
        enabled=req.enabled,
    )
    if skill is None:
        raise HTTPException(404, "技能不存在")
    store.save()
    return _dump(skill)


@router.post("/{skill_id}/update")
async def update_from_source(skill_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = get_skill_store()
    skill = _require_own(store.get(skill_id), ws)
    if not skill.repo:
        raise HTTPException(400, "该技能不是从 GitHub 安装的，无法检查更新")

    try:
        updated, _ = skill_installer.install(
            store, ws, f"https://github.com/{skill.repo}", skill.ref, skill.subpath
        )
    except skill_installer.SkillInstallError as exc:
        _raise_install_error(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"更新失败：{exc}") from exc

    changed = updated.commit != skill.commit
    return {"skill": _dump(updated), "changed": changed, "previous_commit": skill.commit}


@router.get("/{skill_id}/files")
async def list_files(skill_id: str, ws: str = Depends(current_workspace)) -> dict:
    skill = _require_own(get_skill_store().get(skill_id), ws)
    return {"files": skill.files, "source_url": skill.source_url}


@router.get("/{skill_id}/file")
async def read_file(
    skill_id: str, path: str = Query(...), ws: str = Depends(current_workspace)
) -> dict:
    _require_own(get_skill_store().get(skill_id), ws)
    try:
        content, is_text = skill_installer.read_text_file(skill_id, path)
    except skill_installer.SkillInstallError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"path": path, "content": content, "is_text": is_text}


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, ws: str = Depends(current_workspace)) -> dict:
    store = get_skill_store()
    _require_own(store.get(skill_id), ws)
    if not store.delete(skill_id):
        raise HTTPException(400, "技能不存在或为内置技能（不可删除）")
    skill_installer.remove_files(skill_id)
    store.save()
    return {"ok": True}
