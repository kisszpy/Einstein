"""把已有验收脚本纳入 pytest：脚本本身也是代码，坏了要立刻知道。

为什么用子进程而不是 import：这些脚本各自带 `sys.exit(main())` 与独立的
环境假设，import 会污染测试进程（它们会自己连库、自己改配置文件）。子进程
跑一遍既验证脚本能执行，也验证退出码语义（CI 靠退出码判断）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def _run(script: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=timeout,
    )


@pytest.mark.parametrize("script", [
    "verify_contracts.py",     # 协议成员是否被所有实现补齐
])
def test_verify_script_passes(script: str):
    result = _run(script)
    assert result.returncode == 0, f"{script} 失败：\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"


def test_contracts_report_mentions_protocols():
    """退出码 0 还不够：空跑（一个协议都没检查）也会返回 0。"""
    result = _run("verify_contracts.py")
    assert "协议" in result.stdout
