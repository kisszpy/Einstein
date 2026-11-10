"""浏览页产物清单（list_artifacts）：只出白名单后缀，依赖/源码不出现。

背景（2026-11-10 用户反馈）：浏览页此前直接拉全量 /tree，nocturne-cinema
解包 libatomic.deb 后 1121 个文件把产物淹没。修复后 /browser 走
/api/projects/{id}/artifacts，判定用 sandbox.ARTIFACT_SUFFIXES 白名单。
"""

from __future__ import annotations

import pytest

from app.services.capabilities import sandbox


@pytest.fixture
def artifact_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = sandbox.workspace_root("ws1")
    proj = root / "demo"
    (proj / "sub").mkdir(parents=True)
    # 产物
    (proj / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (proj / "README.md").write_text("# demo", encoding="utf-8")
    (proj / "sub" / "chart.svg").write_text("<svg/>", encoding="utf-8")
    # 非产物：源码与二进制
    (proj / "app.js").write_text("console.log(1)", encoding="utf-8")
    (proj / "style.css").write_text("body{}", encoding="utf-8")
    (proj / "libatomic.deb").write_bytes(b"deb")
    # SKIP_DIRS 里的依赖目录：即使后缀是产物也不出现
    (proj / "node_modules" / "pkg").mkdir(parents=True)
    (proj / "node_modules" / "pkg" / "page.html").write_text("<p/>", encoding="utf-8")
    # 点/下划线开头的运行时目录（pip install / 解包残留）：同样不是产物
    (proj / ".cache" / "ms-playwright").mkdir(parents=True)
    (proj / ".cache" / "ms-playwright" / "logo.png").write_bytes(b"png")
    (proj / ".local" / "lib" / "site-packages" / "playwright" / "driver").mkdir(parents=True)
    (proj / ".local" / "lib" / "site-packages" / "playwright" / "driver" / "README.md").write_text(
        "x", encoding="utf-8"
    )
    (proj / "_scratch").mkdir()
    (proj / "_scratch" / "note.md").write_text("x", encoding="utf-8")
    return proj


def test_list_artifacts_only_whitelisted_suffixes(artifact_tree):
    data = sandbox.list_artifacts("ws1", "demo")
    paths = [e["path"] for e in data["entries"]]
    assert paths == ["README.md", "index.html", "sub/chart.svg"]  # 排序稳定
    assert all(e["type"] == "file" for e in data["entries"])
    assert data["count"] == 3
    assert data["truncated"] is False


def test_list_artifacts_skips_runtime_dirs(artifact_tree):
    data = sandbox.list_artifacts("ws1", "demo")
    paths = {e["path"] for e in data["entries"]}
    assert all(
        not p.startswith(("node_modules", ".cache", ".local", "_scratch")) for p in paths
    )


def test_artifacts_shape_matches_tree(artifact_tree):
    """前端 FileTree 直接吃这个形状，字段必须与 /tree 的 entries 一致。"""
    data = sandbox.list_artifacts("ws1", "demo")
    entry = data["entries"][0]
    assert set(entry) == {"path", "type", "size"}
    assert entry["type"] == "file"


def test_list_artifacts_unknown_project(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    with pytest.raises(sandbox.SandboxError):
        sandbox.list_artifacts("ws1", "no-such")


def test_count_artifacts_matches_list(artifact_tree):
    """项目列表的 artifacts 计数必须与产物清单条数同源（浏览页两处数字一致）。"""
    listed = sandbox.list_artifacts("ws1", "demo")
    assert sandbox.count_artifacts(artifact_tree) == listed["count"] == 3
