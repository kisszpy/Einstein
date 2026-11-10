"""项目整包下载（/archive）的打包逻辑。

这里只测纯函数 `_write_archive`——路由层的鉴权与临时文件清理靠 TestClient，
但那部分是中间件行为，另有覆盖。曾经这个端点是「全量进内存 + 事件循环里同步
打包」，几百 MB 的项目会同时顶爆内存和卡住全站，所以压缩策略与一致性要锁住。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.api.routes.projects import _STORE_SUFFIXES, _write_archive


def _tree(root: Path) -> None:
    (root / "deck").mkdir(parents=True)
    (root / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (root / "deck" / "slides").mkdir()
    (root / "deck" / "slides" / "01.svg").write_text("<svg/>", encoding="utf-8")
    # 已压缩格式：再 DEFLATE 一遍只是白烧 CPU
    (root / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 512)
    # 依赖目录：按 SKIP_DIRS 排除，不该出现在包里
    (root / "node_modules").mkdir()
    (root / "node_modules" / "big.js").write_text("x", encoding="utf-8")


def test_archive_keeps_relative_layout(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    src.mkdir()
    _tree(src)
    dest = tmp_path / "out.zip"

    count = _write_archive(src, dest)

    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        assert zf.read("index.html") == b"<h1>hi</h1>"
        assert zf.read("deck/slides/01.svg") == b"<svg/>"

    # index.html + 01.svg + cover.png 共三个；node_modules/big.js 被跳过
    assert count == 3
    assert {"index.html", "cover.png", "deck/slides/01.svg"} <= names
    assert not any("node_modules" in n for n in names)
    # 路径必须是相对 root 的，不能把绝对路径塞进 zip（解包会炸）
    assert all(not n.startswith("/") and ":" not in n for n in names)


def test_already_compressed_suffix_is_stored(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    src.mkdir()
    (src / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096)
    (src / "notes.md").write_text("a" * 4096, encoding="utf-8")
    dest = tmp_path / "out.zip"

    _write_archive(src, dest)

    with zipfile.ZipFile(dest) as zf:
        assert zf.getinfo("cover.png").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("notes.md").compress_type == zipfile.ZIP_DEFLATED

    assert ".png" in _STORE_SUFFIXES and ".pptx" in _STORE_SUFFIXES


def test_unreadable_entry_does_not_abort_archive(tmp_path: Path) -> None:
    """打包途中单个文件读不到，整包不能失败。

    用「写完清单后立刻删掉」模拟——真实场景是智能体还在写、用户点了下载。
    """
    src = tmp_path / "proj"
    src.mkdir()
    (src / "keep.txt").write_text("ok", encoding="utf-8")
    doomed = src / "gone.txt"
    doomed.write_text("bye", encoding="utf-8")
    doomed.unlink()  # iter_files 扫不到它，等价于打包期间消失

    dest = tmp_path / "out.zip"
    count = _write_archive(src, dest)

    with zipfile.ZipFile(dest) as zf:
        assert zf.namelist() == ["keep.txt"]
    assert count == 1
