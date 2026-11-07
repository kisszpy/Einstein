"""批量导入：目录扫描、ZIP 解包、垃圾文件过滤。

真实语料是按目录组织的（产品文档/、接口规范/、会议纪要/…），逐个挑文件不现实。
这里把「一堆文件」统一成 BatchItem 列表，交给同一个摄取任务处理：

- 浏览器上传：前端用 webkitdirectory 或拖拽目录，把相对路径一并带上来
- ZIP 上传：服务端解包，保留内部目录结构
- 服务端目录：直接扫描本机路径，零上传（本地部署场景）

文档名统一取「相对路径」，这样同类文件不会因为重名互相覆盖，
检索结果里也能看出内容来自哪个目录。
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.parsers import SUPPORTED_EXTENSIONS

MAX_BATCH_FILES = 500                                # 单批文件数上限
MAX_BATCH_BYTES = 2 * 1024 * 1024 * 1024             # 单批解压/累计大小上限（防 zip 炸弹）
MAX_NAME_LEN = 200                                   # 文档名上限，避免超长相对路径

# 目录名黑名单：版本库、依赖、编辑器缓存、系统回收站
SKIP_DIRS = {
    "__MACOSX", ".git", ".svn", ".hg", "node_modules", ".venv", "venv",
    "__pycache__", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "$RECYCLE.BIN", "System Volume Information",
}
# 文件名黑名单：系统缩略图/占位文件
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep", ".gitignore"}

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class BatchItem:
    """一个待摄取的条目：磁盘路径 + 作为文档名的相对路径。"""

    path: Path
    name: str
    size: int


@dataclass(frozen=True)
class SkippedItem:
    """被跳过的条目与原因，返回给前端让用户知道「为什么少了几篇」。"""

    name: str
    reason: str

    def as_dict(self) -> dict:
        return {"name": self.name, "reason": self.reason}


def is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def clean_rel_path(raw: str) -> str:
    """把前端传来的相对路径规范化成安全的 posix 相对路径。

    去掉盘符、开头的 /、以及任何 .. 段；空路径返回空串（调用方按无效处理）。
    """
    text = (raw or "").strip().replace("\\", "/")
    text = _DRIVE.sub("", text)
    parts = [p for p in PurePosixPath(text).parts if p not in ("", ".", "..", "/")]
    rel = "/".join(parts)
    return rel[:MAX_NAME_LEN]


def is_ignored(rel: str) -> str:
    """返回非空字符串表示应跳过（内容为原因）。"""
    parts = PurePosixPath(rel).parts
    if not parts:
        return "路径为空"
    for part in parts[:-1]:
        if part in SKIP_DIRS or part.startswith("."):
            return f"跳过目录 {part}/"
    leaf = parts[-1]
    if leaf in SKIP_FILES or leaf.startswith("."):
        return "隐藏文件"
    if not is_supported(leaf):
        return f"不支持的格式（{Path(leaf).suffix or '无扩展名'}）"
    return ""


def extract_zip(
    src: Path, dest: Path, *, max_files: int = MAX_BATCH_FILES,
    max_bytes: int = MAX_BATCH_BYTES,
) -> tuple[list[BatchItem], list[SkippedItem]]:
    """解开 ZIP 到 dest，按 zip 内相对路径生成 BatchItem（文档名保留目录结构）。

    安全约束：拒绝加密条目、符号链接、绝对路径与 .. 穿越；限制条目数与解压后总大小。
    dest 只是落盘暂存区，文件名用扁平序号，真实相对路径记录在 BatchItem.name 里，
    避免把用户给的路径直接拼到文件系统上。
    """
    items: list[BatchItem] = []
    skipped: list[SkippedItem] = []
    dest.mkdir(parents=True, exist_ok=True)
    total = 0

    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                rel = clean_rel_path(info.filename)
            except Exception:  # noqa: BLE001 — 病理文件名不影响其它条目
                skipped.append(SkippedItem(info.filename, "文件名非法"))
                continue
            reason = is_ignored(rel)
            if reason:
                skipped.append(SkippedItem(rel, reason))
                continue
            if info.flag_bits & 0x1:
                skipped.append(SkippedItem(rel, "加密条目无法读取"))
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # 符号链接
                skipped.append(SkippedItem(rel, "符号链接已跳过"))
                continue
            if len(items) >= max_files:
                skipped.append(SkippedItem(rel, f"超出单批 {max_files} 个文件上限"))
                continue
            if info.file_size > max_bytes:
                skipped.append(SkippedItem(rel, "单文件过大"))
                continue
            total += info.file_size
            if total > max_bytes:
                skipped.append(SkippedItem(rel, "解压后总大小超过上限"))
                continue
            target = dest / f"{len(items):05d}{Path(rel).suffix.lower()}"
            with zf.open(info) as fh, target.open("wb") as out:
                while chunk := fh.read(1024 * 1024):
                    out.write(chunk)
            items.append(BatchItem(target, rel, info.file_size))

    return items, skipped


def scan_directory(
    root: Path, *, recursive: bool = True, skip_unsupported: bool = True,
    max_files: int = MAX_BATCH_FILES, max_bytes: int = MAX_BATCH_BYTES,
) -> tuple[list[BatchItem], list[SkippedItem]]:
    """扫描服务端目录，返回可直接摄取的条目。

    文件就地引用、不复制不入库删除，所以源目录不会被改动。
    跳过符号链接（防止绕出根目录）与隐藏/依赖目录。
    """
    items: list[BatchItem] = []
    skipped: list[SkippedItem] = []
    root = root.resolve()
    total = 0

    def walk(current: Path, depth: int) -> None:
        nonlocal total
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError as exc:
            skipped.append(SkippedItem(str(current), f"无法读取目录：{exc.strerror or exc}"))
            return
        for entry in entries:
            if entry.is_symlink():
                skipped.append(SkippedItem(entry.path, "符号链接已跳过"))
                continue
            if entry.is_dir(follow_symlinks=False):
                if not recursive:
                    continue
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue
                walk(Path(entry.path), depth + 1)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            rel = clean_rel_path(str(Path(entry.path).relative_to(root)))
            reason = is_ignored(rel)
            if reason:
                if not (skip_unsupported and reason.startswith("不支持")):
                    skipped.append(SkippedItem(rel, reason))
                continue
            if len(items) >= max_files:
                skipped.append(SkippedItem(rel, f"超出单批 {max_files} 个文件上限"))
                continue
            size = entry.stat(follow_symlinks=False).st_size
            total += size
            if total > max_bytes:
                skipped.append(SkippedItem(rel, "累计大小超过上限"))
                continue
            items.append(BatchItem(Path(entry.path), rel, size))

    walk(root, 0)
    return items, skipped


def resolve_allowed_root(raw: str, allowed_roots: list[str]) -> Path:
    """校验服务端目录是否在允许范围内，返回解析后的真实路径。

    目录导入等于「让服务跑起来的人读本机任意文件」，所以默认关闭：
    只有显式配置了 APP_LOCAL_IMPORT_ROOTS 才放行，且必须落在其中一个根下
    （用 resolve() 后的真实路径比较，防止 ../ 与符号链接绕过）。
    """
    if not allowed_roots:
        raise PermissionError(
            "服务端目录导入未启用：需要在 .env 里配置 APP_LOCAL_IMPORT_ROOTS"
            "（逗号分隔的绝对路径），或改用「上传文件夹」。"
        )
    if not raw or not raw.strip():
        raise ValueError("目录路径不能为空")
    target = Path(raw.strip()).expanduser()
    if not target.is_absolute():
        raise ValueError("请填写绝对路径")
    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"目录不存在：{raw}") from exc
    if not resolved.is_dir():
        raise ValueError(f"不是目录：{raw}")
    for root in allowed_roots:
        try:
            allowed = Path(root).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if resolved == allowed or resolved.is_relative_to(allowed):
            return resolved
    raise PermissionError(
        f"目录不在允许范围内（APP_LOCAL_IMPORT_ROOTS）：{resolved}"
    )
