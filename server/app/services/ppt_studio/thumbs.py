"""生成结果缩略图缓存：结果卡要能「一眼看到成品」，翻历史时也要还在。

为什么不塞进生成记录：一份 14 页的缩略图约 500KB，base64 进 SQLite 后每次
`GET /generations` 都要拖 700KB×N 条记录。落磁盘 + 按页取，列表只带一个计数。

为什么不放进项目 sandbox：那是用户的成果目录，不该被平台缓存文件污染，
也不该占用户的容量配额。这里放在服务端 data/ 下，按生成 id 分目录，
超过上限自动淘汰最旧的（缓存丢了只是没缩略图，deck 本身不受影响）。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "ppt_thumbs"
MAX_GENERATIONS = 200
_lock = threading.Lock()


def _dir(generation_id: str) -> Path:
    safe = "".join(ch for ch in generation_id if ch.isalnum() or ch in "_-")[:40]
    return _DATA_DIR / safe


def save(generation_id: str, images: list[bytes]) -> int:
    """落盘一份生成的全部缩略图，返回实际写出的张数。空字节跳过（该页没截到图）。"""
    if not generation_id or not any(images):
        return 0
    folder = _dir(generation_id)
    with _lock:
        folder.mkdir(parents=True, exist_ok=True)
        written = 0
        for index, blob in enumerate(images):
            if not blob:
                continue
            (folder / f"{index:03d}.jpg").write_bytes(blob)
            written += 1
        _prune()
    return written


def load(generation_id: str, index: int) -> bytes | None:
    if index < 0 or index > 999:
        return None
    target = _dir(generation_id) / f"{index:03d}.jpg"
    try:
        return target.read_bytes() if target.is_file() else None
    except OSError:
        return None


def count(generation_id: str) -> int:
    folder = _dir(generation_id)
    if not folder.is_dir():
        return 0
    return sum(1 for _ in folder.glob("*.jpg"))


def drop(generation_id: str) -> None:
    shutil.rmtree(_dir(generation_id), ignore_errors=True)


def _prune() -> None:
    """只保留最近 MAX_GENERATIONS 份。调用方已持锁。"""
    folders = [p for p in _DATA_DIR.iterdir() if p.is_dir()] if _DATA_DIR.is_dir() else []
    if len(folders) <= MAX_GENERATIONS:
        return
    folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in folders[MAX_GENERATIONS:]:
        shutil.rmtree(old, ignore_errors=True)


__all__ = ["save", "load", "count", "drop", "MAX_GENERATIONS"]