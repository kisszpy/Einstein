"""备份工具的保留策略与恢复演练。

这里不测 pg_dump 本身（要真数据库），测的是那条最容易烂掉的链路：
"备份文件长得像备份，里面其实是空的"。verify 必须对这种包说不。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_tool():
    spec = importlib.util.spec_from_file_location("backup_tool", ROOT / "tools" / "backup.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


backup_tool = _load_tool()


def _make_archive(path: Path, *, files: dict[str, bytes], manifest: dict | None = None) -> Path:
    """按归档真实布局造一个包：<root>/einstein/{manifest.json + files}。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "einstein"
        stage.mkdir()
        for name, payload in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        if manifest is not None:
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
        with tarfile.open(path, "w:gz") as tar:
            tar.add(stage, arcname="einstein")
    return path


def _sqlite_bytes(tables: int = 2) -> bytes:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        db_path = Path(fh.name)
    conn = sqlite3.connect(db_path)
    try:
        for i in range(tables):
            conn.execute(f"CREATE TABLE t{i} (id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()
    finally:
        conn.close()
    payload = db_path.read_bytes()
    db_path.unlink(missing_ok=True)
    return payload


def _ok(checks: list[tuple[bool, str]]) -> bool:
    return all(passed for passed, _ in checks)


def test_verify_passes_on_healthy_sqlite_archive(tmp_path):
    archive = _make_archive(
        tmp_path / "einstein-x-sqlite.tar.gz",
        files={"einstein.db": _sqlite_bytes()},
        manifest={"backend": "sqlite", "created_at": "2026-11-09", "files": ["einstein.db"]},
    )
    checks = backup_tool.verify(archive)
    assert _ok(checks), checks


def test_verify_rejects_archive_without_manifest(tmp_path):
    archive = _make_archive(
        tmp_path / "einstein-y-sqlite.tar.gz", files={"einstein.db": _sqlite_bytes()},
    )
    checks = backup_tool.verify(archive)
    assert not _ok(checks)
    assert any("manifest" in msg for _, msg in checks)


def test_verify_rejects_missing_listed_file(tmp_path):
    archive = _make_archive(
        tmp_path / "einstein-z-json.tar.gz",
        files={"a.json": b"{}"},
        manifest={"backend": "json", "files": ["a.json", "b.json"]},
    )
    checks = backup_tool.verify(archive)
    assert not _ok(checks)
    assert any("都在归档里" in msg for _, msg in checks)


def test_verify_rejects_zero_byte_file(tmp_path):
    archive = _make_archive(
        tmp_path / "einstein-w-json.tar.gz",
        files={"a.json": b"", "b.json": b"{}"},
        manifest={"backend": "json", "files": ["a.json", "b.json"]},
    )
    assert not _ok(backup_tool.verify(archive))


def test_verify_rejects_broken_json(tmp_path):
    archive = _make_archive(
        tmp_path / "einstein-v-json.tar.gz",
        files={"a.json": b"{not json"},
        manifest={"backend": "json", "files": ["a.json"]},
    )
    assert not _ok(backup_tool.verify(archive))


def test_verify_rejects_empty_pg_dump(tmp_path):
    archive = _make_archive(
        tmp_path / "einstein-u-postgres.tar.gz",
        files={"postgres.sql": b"-- nothing here\n"},
        manifest={"backend": "postgres", "files": ["postgres.sql"]},
    )
    assert not _ok(backup_tool.verify(archive))


def test_verify_missing_file(tmp_path):
    checks = backup_tool.verify(tmp_path / "nope.tar.gz")
    assert checks and checks[0][0] is False


# ---------------------------------------------------------------- 保留策略


@pytest.fixture()
def backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_tool, "BACKUP_DIR", tmp_path)
    return tmp_path


def _touch(directory: Path, name: str, age_days: float, size: int = 1024) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    import os

    os.utime(path, (stamp, stamp))
    return path


def test_prune_keeps_newest_n(backup_dir):
    for i in range(5):
        _touch(backup_dir, f"einstein-2026110{i}-000000-sqlite.tar.gz", age_days=5 - i)
    dropped, freed = backup_tool.prune(keep=2)
    assert dropped == 3
    assert freed > 0
    left = sorted(p.name for p in backup_dir.glob("*.tar.gz"))
    assert len(left) == 2
    # 留下的是最新的两份
    assert left == [
        "einstein-20261103-000000-sqlite.tar.gz",
        "einstein-20261104-000000-sqlite.tar.gz",
    ]


def test_prune_by_age_removes_stale_even_if_few(backup_dir):
    _touch(backup_dir, "einstein-old-sqlite.tar.gz", age_days=40)
    _touch(backup_dir, "einstein-new-sqlite.tar.gz", age_days=1)
    dropped, _ = backup_tool.prune(keep=10, days=30)
    assert dropped == 1
    assert [p.name for p in backup_dir.glob("*.tar.gz")] == ["einstein-new-sqlite.tar.gz"]


def test_prune_noop_when_nothing_to_drop(backup_dir):
    _touch(backup_dir, "einstein-a-sqlite.tar.gz", age_days=1)
    assert backup_tool.prune(keep=5) == (0, 0)
