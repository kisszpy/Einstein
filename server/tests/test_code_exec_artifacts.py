"""代码执行的「产物识别」：跑完到底该交回哪些文件。

这一组守的是用户能直接看见的那件事：**沙箱里生成的文件有没有回到项目、
有没有变成前端卡片**。三种后端（云 / 容器 / 宿主）的同步机制不一样——
云靠回写清单，容器与宿主的文件是直接落盘的——但对外必须是同一个结果，
否则换个部署方式，同一段代码就「有时交付、有时不交付」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.capabilities import code_exec


def _tree(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (root / ".agent_tmp").mkdir(exist_ok=True)
    (root / "node_modules").mkdir(exist_ok=True)
    (root / "node_modules" / "big.js").write_text("x" * 100, encoding="utf-8")


# ---------------------------------------------------------------- 快照


def test_snapshot_skips_dependency_dirs(tmp_path: Path) -> None:
    _tree(tmp_path)
    snap = code_exec._snapshot(tmp_path)
    assert "src/main.py" in snap
    assert not any("node_modules" in rel for rel in snap), "几百 MB 依赖不该进快照"
    assert not any(".agent_tmp" in rel for rel in snap), "临时脚本不是产物"


def test_produced_reports_new_and_changed_only(tmp_path: Path) -> None:
    _tree(tmp_path)
    before = code_exec._snapshot(tmp_path)

    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.md").write_text("hi", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text("print(2)\n", encoding="utf-8")

    produced = code_exec._produced(tmp_path, before)
    assert produced == ["out/report.md", "src/main.py"]


def test_produced_ignores_untouched_files(tmp_path: Path) -> None:
    _tree(tmp_path)
    before = code_exec._snapshot(tmp_path)
    assert code_exec._produced(tmp_path, before) == [], "什么都没改就不该报产物"


# ---------------------------------------------------------------- 宿主后端


@pytest.mark.skipif(sys.platform.startswith("win") is False, reason="仅在本机验证宿主路径")
def test_host_run_reports_produced_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主后端没有回写清单，只能靠前后快照分辨产物。"""
    _tree(tmp_path)
    monkeypatch.setattr(code_exec, "resolve_mode", lambda: "host")
    monkeypatch.setattr(code_exec.sandbox, "project_dir", lambda *a, **k: tmp_path)

    res = code_exec.run_code(
        "ws", "proj",
        code="import os\nos.makedirs('out', exist_ok=True)\nopen('out/x.md','w').write('hi')\n",
        language="python",
    )

    assert res["ok"], res["stderr"]
    assert res["files"] == ["out/x.md"]
    assert (tmp_path / "out" / "x.md").read_text(encoding="utf-8") == "hi"
    # 本次的临时脚本不能被当成交付物
    assert not any(".agent_tmp" in rel for rel in res["files"])


def test_host_run_keeps_files_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非零退出也要把已写出的文件交回来：失败不等于没有产物。"""
    _tree(tmp_path)
    monkeypatch.setattr(code_exec, "resolve_mode", lambda: "host")
    monkeypatch.setattr(code_exec.sandbox, "project_dir", lambda *a, **k: tmp_path)

    res = code_exec.run_code(
        "ws", "proj",
        code="open('partial.csv','w').write('a,b')\nraise SystemExit(2)\n",
        language="python",
    )

    assert res["ok"] is False
    assert res["files"] == ["partial.csv"]
    assert (tmp_path / "partial.csv").read_text(encoding="utf-8") == "a,b"
