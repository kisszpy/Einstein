"""二进制成品写入（write_bytes）：演示编辑器保存回传的 pptx 走这条通道。

文本 /file 是 UTF-8 + 2MB，pptx 是 zip，塞不进去；/file-bytes 按 base64 收、
只放行成品后缀、单文件 32MB。这里守住三件事：字节完整、覆盖时报告旧体积、
非法输入（坏 base64 / 非成品后缀 / 路径穿越）一律拒。
"""

from __future__ import annotations

import base64

import pytest

from app.services.capabilities import sandbox


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    (sandbox.workspace_root("ws1") / "demo").mkdir(parents=True)
    return "demo"


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_write_bytes_roundtrip(project):
    raw = b"PK\x03\x04fake-pptx-body"
    out = sandbox.write_bytes("ws1", project, "deck.pptx", b64(raw))
    assert out["bytes"] == len(raw)
    assert out["created"] is True
    assert (sandbox.project_dir("ws1", project) / "deck.pptx").read_bytes() == raw


def test_write_bytes_overwrites_and_reports_previous(project):
    target = sandbox.project_dir("ws1", project) / "deck.pptx"
    target.write_bytes(b"old")
    out = sandbox.write_bytes("ws1", project, "deck.pptx", b64(b"newer"))
    assert out["created"] is False
    assert out["previous_bytes"] == 3
    assert target.read_bytes() == b"newer"


def test_write_bytes_rejects_non_artifact_suffix(project):
    with pytest.raises(sandbox.SandboxError):
        sandbox.write_bytes("ws1", project, "run.sh", b64(b"echo hi"))


def test_write_bytes_rejects_bad_base64(project):
    with pytest.raises(sandbox.SandboxError):
        sandbox.write_bytes("ws1", project, "deck.pptx", "not base64!!")


def test_write_bytes_rejects_traversal(project):
    with pytest.raises(sandbox.SandboxError):
        sandbox.write_bytes("ws1", project, "../escape.pptx", b64(b"x"))


def test_write_bytes_creates_parent_dirs(project):
    out = sandbox.write_bytes("ws1", project, "out/decks/a.pptx", b64(b"PK"))
    assert out["created"] is True
    assert (sandbox.project_dir("ws1", project) / "out" / "decks" / "a.pptx").exists()
