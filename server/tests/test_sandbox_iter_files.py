"""沙箱遍历对「不可访问条目」的容错：Windows 悬空符号链接不再炸列表。

背景（2026-11-10 事故）：智能体解包 .deb 在项目里留下 Linux 软链
（nocturne-cinema/libx/.../libatomic1 -> gcc-14-base，目标不存在），
Windows 对这类条目 stat 抛 OSError(WinError 1920)，`Path.is_file()`
不吞这个错误码直接炸，/api/projects 全量 500——网站「挂了」的根因。
修复后项目目录上的递归统计一律走 sandbox.iter_files / _stat_safe，
这里钉死语义：坏条目被跳过，列表/树/统计照常出数。

两条测试路径：
1. monkeypatch 模拟 WinError 1920——任何环境都能跑，忠实复现错误码；
2. 真实悬空链接（symlink 优先，Windows 无权限时退 junction）——验证真实文件系统行为。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.capabilities import sandbox


class _WinError1920(OSError):
    """生产事故里悬空软链 stat 的真实形态（ERROR_CANT_ACCESS_FILE）。"""

    def __init__(self) -> None:
        super().__init__(1920, "系统无法访问此文件。")


@pytest.fixture
def project_tree(tmp_path, monkeypatch):
    """一个普通项目目录，外加一个「stat 必炸」的毒条目。"""
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = sandbox.workspace_root("ws1")
    proj = root / "demo"
    (proj / "sub").mkdir(parents=True)
    (proj / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (proj / "sub" / "a.txt").write_text("abc", encoding="utf-8")
    (proj / "poison-file").write_text("should be skipped", encoding="utf-8")

    orig_stat = Path.stat

    def poisoned_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "poison-file":
            raise _WinError1920()
        return orig_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", poisoned_stat)
    return proj


# ---------------------------------------------------------------- 路径 1：模拟 WinError 1920


def test_iter_files_skips_unstatable_entry(project_tree):
    found = {p.name for p, _st in sandbox.iter_files(project_tree)}
    assert found == {"index.html", "a.txt"}  # 毒条目被跳过，其余照常枚举


def test_dir_size_with_unstatable_entry(project_tree):
    assert sandbox.dir_size(project_tree) == len("<h1>hi</h1>") + len("abc")


def test_list_files_with_unstatable_entry(project_tree):
    data = sandbox.list_files("ws1", "demo")
    paths = {e["path"] for e in data["entries"]}
    assert "index.html" in paths
    assert all("poison-file" not in p for p in paths)  # 坏条目不进树，也不抛错


# ---------------------------------------------------------------- 路径 2：真实悬空链接


def _make_dangling_link(proj: Path) -> Path:
    """优先 symlink（最贴近 .deb 解包产物）；无权限时 Windows 退 junction。
    都不行返回空路径，调用方 skip。"""
    link = proj / "dangling-link"
    try:
        os.symlink(proj / "no-such-target", link)
        return link
    except OSError:
        pass
    if sys.platform == "win32":
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(proj / "no-such-target")],
            capture_output=True,
        )
        if r.returncode == 0 and os.path.lexists(link):
            return link  # junction 创建成功且目标不存在：悬空链接到手
    return Path()


def test_iter_files_with_real_dangling_link(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = sandbox.workspace_root("ws2")
    proj = root / "demo2"
    proj.mkdir(parents=True)
    (proj / "ok.txt").write_text("fine", encoding="utf-8")
    link = _make_dangling_link(proj)
    if not link.name:
        pytest.skip("当前环境无法创建悬空链接（symlink 需管理员/开发者模式）")

    found = {p.name for p, _st in sandbox.iter_files(proj)}
    assert found == {"ok.txt"}  # 悬空链接被跳过
