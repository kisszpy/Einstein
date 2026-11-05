"""项目工作台 API：列表、创建、文件树、读写、单文件下载、整包下载。"""

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.capabilities import sandbox

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str
    template: str = "blank"
    description: str = ""


class FileWriteRequest(BaseModel):
    path: str
    content: str


def _guard(exc: sandbox.SandboxError, code: int = 400) -> HTTPException:
    return HTTPException(code, str(exc))


@router.get("")
async def list_projects(ws: str = Depends(current_workspace)) -> list[dict]:
    return sandbox.list_projects(ws)


@router.post("")
async def create_project(req: ProjectCreateRequest, ws: str = Depends(current_workspace)) -> dict:
    try:
        return sandbox.create_project(ws, req.name, req.template, req.description)
    except sandbox.SandboxError as exc:
        raise _guard(exc) from exc


@router.delete("/{project}")
async def delete_project(project: str, ws: str = Depends(current_workspace)) -> dict:
    try:
        ok = sandbox.delete_project(ws, project)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.get("/{project}/tree")
async def project_tree(project: str, ws: str = Depends(current_workspace)) -> dict:
    try:
        return sandbox.list_files(ws, project)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.get("/{project}/file")
async def read_file(project: str, path: str, ws: str = Depends(current_workspace)) -> dict:
    try:
        return sandbox.read_file(ws, project, path)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.put("/{project}/file")
async def write_file(project: str, req: FileWriteRequest, ws: str = Depends(current_workspace)) -> dict:
    try:
        return sandbox.write_file(ws, project, req.path, req.content)
    except sandbox.SandboxError as exc:
        raise _guard(exc) from exc


@router.get("/{project}/download")
async def download_file(project: str, path: str, ws: str = Depends(current_workspace)) -> FileResponse:
    try:
        root = sandbox.project_dir(ws, project)
        target = sandbox.resolve_in(root, path)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{path}")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.get("/{project}/archive")
async def download_archive(project: str, ws: str = Depends(current_workspace)) -> StreamingResponse:
    try:
        root = sandbox.project_dir(ws, project)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(root.rglob("*")):
            if file.is_file() and ".agent_tmp" not in file.parts:
                zf.write(file, file.relative_to(root))
    buf.seek(0)
    # RFC 5987：中文项目名放进 filename*，ASCII 名做 fallback
    from urllib.parse import quote

    ascii_name = project.encode("ascii", "ignore").decode() or "project"
    disposition = (
        f"attachment; filename=\"{ascii_name}.zip\"; "
        f"filename*=UTF-8''{quote(project)}.zip"
    )
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )
