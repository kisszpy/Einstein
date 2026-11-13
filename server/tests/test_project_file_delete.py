"""资产列表的文件删除：只删那一个文件，不误伤目录与项目外路径。

背景（2026-11-13 用户反馈）：资产越来越多，没有清理入口。删除按钮挂在每个
文件行上，所以「删错范围」的代价很高——这里把边界钉死。
"""

from __future__ import annotations

import pytest

from app.services.capabilities import sandbox


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = sandbox.workspace_root("ws1") / "demo"
    (root / "deck").mkdir(parents=True)
    (root / "deck" / "a.pptx").write_bytes(b"x" * 10)
    (root / "keep.md").write_text("keep", encoding="utf-8")
    return root


def test_delete_removes_only_that_file(project):
    out = sandbox.delete_file("ws1", "demo", "deck/a.pptx")
    assert out == {"path": "deck/a.pptx", "bytes": 10, "total_files": 1}
    assert not (project / "deck" / "a.pptx").exists()
    assert (project / "keep.md").read_text(encoding="utf-8") == "keep"


def test_delete_refuses_directories(project):
    with pytest.raises(sandbox.SandboxError):
        sandbox.delete_file("ws1", "demo", "deck")
    assert (project / "deck").is_dir()


def test_delete_refuses_the_project_root(project):
    with pytest.raises(sandbox.SandboxError):
        sandbox.delete_file("ws1", "demo", "")
    assert project.is_dir()


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "C:/Windows/win.ini"])
def test_delete_refuses_paths_outside_the_project(project, path):
    with pytest.raises(sandbox.SandboxError):
        sandbox.delete_file("ws1", "demo", path)


def test_delete_reports_a_missing_file(project):
    with pytest.raises(sandbox.SandboxError):
        sandbox.delete_file("ws1", "demo", "deck/ghost.pptx")
