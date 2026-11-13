"""项目工作台 API：列表、创建、文件树、读写、单文件下载、整包下载。

浏览相关的两个端点专供内置浏览器：
- `/file/{ticket}/{path}` 原样吐文件（MIME 正确），HTML 里的相对资源靠它；
- `/preview` 给 HTML 注入 base，让静态站点在 iframe 里真的能跑起来。
它们用票据鉴权（iframe 带不了 Authorization 头），票据由 /api/browse/ticket 签发。
"""

import asyncio
import mimetypes
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.api.deps import current_uid, current_workspace
from app.services import browser, projects
from app.services.capabilities import deck as deck_service, sandbox

router = APIRouter(prefix="/projects", tags=["projects"])

# 预览要正确 MIME，浏览器才会当 HTML/样式/图片处理；代码类一律当纯文本
_TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".jsx", ".vue", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".env", ".log", ".diff", ".patch",
}
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".mp4": "video/mp4",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _mime_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MIME:
        return _MIME[suffix]
    if suffix in _TEXT_SUFFIXES:
        return "text/plain; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class ProjectCreateRequest(BaseModel):
    name: str
    template: str = "blank"
    description: str = ""


class FileWriteRequest(BaseModel):
    path: str
    content: str


class FileBytesRequest(BaseModel):
    """二进制成品写入：内容按 base64 传（pptx 是 zip，塞不进 JSON 字符串的 UTF-8 路径）。"""

    path: str
    content_base64: str


def _guard(exc: sandbox.SandboxError, code: int = 400) -> HTTPException:
    return HTTPException(code, str(exc))


def _require(ws: str, project: str, uid: str) -> None:
    """项目归属校验：不是自己的项目一律 404（不泄露存在性）。"""
    try:
        projects.require(ws, project, uid)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.get("")
def list_projects(ws: str = Depends(current_workspace), uid: str = Depends(current_uid)) -> list[dict]:
    """当前用户可见的项目。别人创建的项目、平台运行时目录都不会出现。"""
    return projects.list_projects(ws, uid)


@router.post("")
def create_project(
    req: ProjectCreateRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    try:
        return projects.create_project(ws, req.name, req.template, req.description, owner_id=uid)
    except sandbox.SandboxError as exc:
        raise _guard(exc) from exc


@router.delete("/{project}")
def delete_project(
    project: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    try:
        ok = projects.delete_project(ws, project, uid)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.get("/{project}/tree")
def project_tree(
    project: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    _require(ws, project, uid)
    try:
        return sandbox.list_files(ws, project)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.get("/{project}/artifacts")
def project_artifacts(
    project: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    """浏览页「产物」清单：只返回白名单后缀的成品文件，源码与依赖目录不出现。

    与 /tree 的区别：tree 是项目全部文件（智能体工作区用），这里只给
    浏览页要预览的产物。返回形状与 tree 相同。
    """
    _require(ws, project, uid)
    try:
        return sandbox.list_artifacts(ws, project)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.get("/{project}/file")
def read_file(
    project: str, path: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    _require(ws, project, uid)
    try:
        return sandbox.read_file(ws, project, path)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.put("/{project}/file")
def write_file(
    project: str, req: FileWriteRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    _require(ws, project, uid)
    try:
        return sandbox.write_file(ws, project, req.path, req.content)
    except sandbox.SandboxError as exc:
        raise _guard(exc) from exc


@router.put("/{project}/file-bytes")
def write_file_bytes(
    project: str, req: FileBytesRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    """写入二进制成品（演示文稿编辑器保存回传的 pptx 字节）。

    和 `/file` 分开是因为两者约束不同：文本走 UTF-8、限 2MB；
    成品可能是几十 MB 的 zip，走 base64 + 32MB 上限，且只放行成品后缀。
    """
    _require(ws, project, uid)
    try:
        return sandbox.write_bytes(ws, project, req.path, req.content_base64)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 400) from exc


@router.delete("/{project}/file")
def delete_project_file(
    project: str, path: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    """删除项目里的一个文件（资产列表的清理入口）。"""
    _require(ws, project, uid)
    try:
        return sandbox.delete_file(ws, project, path)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 400) from exc


@router.get("/{project}/download")
def download_file(
    project: str, path: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> FileResponse:
    try:
        target = projects.resolve_rel(ws, project, path, uid)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{path}")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
    )


# 已压缩格式再 DEFLATE 一遍只是白烧 CPU（常常还更大），直接 STORED
_STORE_SUFFIXES = {
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".mp4", ".mp3", ".woff", ".woff2",
    ".docx", ".xlsx", ".pptx", ".pdf",
}


def _write_archive(root: Path, dest: Path) -> int:
    """把项目树打成 zip 写到 dest，返回写入的文件数。

    纯磁盘/CPU 活，由调用方丢进线程执行：整包几百 MB 时同步跑会把
    事件循环（含 SSE 流式对话）一起卡住。
    """
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        # iter_files 跳过不可访问条目（悬空软链等），打包不再因单个坏文件 500
        for file, _st in sorted(sandbox.iter_files(root), key=lambda item: item[0]):
            if set(file.parts) & sandbox.SKIP_DIRS:
                continue
            method = (
                zipfile.ZIP_STORED if file.suffix.lower() in _STORE_SUFFIXES
                else zipfile.ZIP_DEFLATED
            )
            try:
                zf.write(file, file.relative_to(root), compress_type=method)
                count += 1
            except OSError:
                # 打包途中文件被删/占用：跳过，不整包失败
                continue
    return count


@router.get("/{project}/archive")
async def download_archive(
    project: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> FileResponse:
    _require(ws, project, uid)
    try:
        root = sandbox.project_dir(ws, project)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc

    # 落临时文件而不是全量进内存：几百 MB 的项目包进 BytesIO 会直接顶爆进程
    fd, tmp_name = tempfile.mkstemp(prefix="proj-archive-", suffix=".zip")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        await asyncio.to_thread(_write_archive, root, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    # RFC 5987：中文项目名放进 filename*，ASCII 名做 fallback
    ascii_name = project.encode("ascii", "ignore").decode() or "project"
    disposition = (
        f"attachment; filename=\"{ascii_name}.zip\"; "
        f"filename*=UTF-8''{quote(project)}.zip"
    )
    return FileResponse(
        tmp,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
        background=BackgroundTask(tmp.unlink, missing_ok=True),
    )


# ---------- 内置浏览器：产物预览 ----------

# 产物 HTML 在应用同源下运行，必须按不透明源隔离：拿不到 localStorage
# 里的登录令牌，也碰不到父窗口 DOM。ticket 在 URL 里，够它取自己的资源。
_SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals"
_PREVIEW_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _inline_response(target: Path) -> Response:
    # 流式回源，不把文件读进内存：`Response(content=target.read_bytes())` 在预览
    # 几百 MB 的 mp4 时会先吃满内存再开始响应，整段读取还卡在事件循环里。
    # FileResponse 顺带支持 Range，视频可以拖进度条。
    headers = dict(_PREVIEW_HEADERS)
    media = _mime_of(target)
    if media.startswith("text/html"):
        # serve_asset 也会走到这里吐 HTML：不套 CSP 的话，产物页面在同源下
        # 就能读 localStorage 里的登录令牌。这层隔离不能省。
        headers["Content-Security-Policy"] = _SANDBOX_CSP
    return FileResponse(target, media_type=media, headers=headers)


@router.get("/{project}/preview")
def preview_file(
    project: str,
    path: str,
    ticket: str = "",
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> Response:
    """预览入口：HTML 注入 base 后渲染（沙箱隔离），其他类型按 MIME 内联返回。"""
    try:
        target = projects.resolve_rel(ws, project, path, uid)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{path}")

    media = _mime_of(target)
    if not media.startswith("text/html"):
        return _inline_response(target)

    # 相对资源要解析到 /file/{ticket}/<同目录>，否则 style.css 会打到 /api/projects/<p>/style.css
    rel_dir = path.rsplit("/", 1)[0] if "/" in path else ""
    base = f"/api/projects/{quote(project, safe='')}/file/{ticket or '-'}"
    if rel_dir:
        base += "/" + quote(rel_dir, safe="/")
    base += "/"
    html = browser.inject_base(target.read_text("utf-8", errors="replace"), base)
    headers = dict(_PREVIEW_HEADERS)
    headers["Content-Security-Policy"] = _SANDBOX_CSP
    return Response(content=html.encode("utf-8"), media_type=media, headers=headers)


@router.get("/{project}/file/{ticket}/{file_path:path}")
def serve_asset(
    project: str,
    ticket: str,
    file_path: str,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> Response:
    """静态资源与站内跳转的落点（ticket 由中间件校验，这里只是路径占位）。"""
    try:
        target = projects.resolve_rel(ws, project, file_path, uid)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{file_path}")
    return _inline_response(target)


# ---------- 演示文稿：可编辑模型 ----------

class DeckSaveRequest(BaseModel):
    deck: dict


class DeckDslRequest(BaseModel):
    page_index: int
    dsl: str
    apply: bool = True


@router.put("/{project}/deck/dsl")
async def apply_deck_dsl(
    project: str,
    path: str,
    req: DeckDslRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """slidep 产物的编辑链路：lint 校验 → 通过则重编译替换该页并刷新预览。"""
    try:
        root = projects.require(ws, project, uid)
        # apply 成功会触发 load_deck → COM 导出预览图（秒级），放线程避免阻塞事件循环
        return await asyncio.to_thread(
            deck_service.apply_dsl_page, root, path, req.page_index, req.dsl, req.apply
        )
    except sandbox.SandboxError as exc:
        raise _guard(exc, 400) from exc


@router.get("/{project}/deck")
async def read_deck(
    project: str, path: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    try:
        root = projects.require(ws, project, uid)
        # load_deck 首次会触发 PowerPoint COM 导出预览图（秒级），放线程避免阻塞事件循环
        return await asyncio.to_thread(deck_service.load_deck, root, path)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 404) from exc


@router.put("/{project}/deck")
def write_deck(
    project: str,
    path: str,
    req: DeckSaveRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    try:
        root = projects.require(ws, project, uid)
        return deck_service.save_deck(root, path, req.deck)
    except sandbox.SandboxError as exc:
        raise _guard(exc, 400) from exc
