"""slidep 桥接：把 WorkBuddy 系的 DSL→PPTX 编译器包装成平台工具。

slidep 是一个本地 Node CLI（slidep.cmd，自带 node.exe），核心能力：
- ``upsert-dsl``：把一页 JSX 风格的 .slide DSL 编译成 OOXML 插入/替换到 PPTX；
- ``lint``：对 DSL 做本地布局检查（溢出/重叠/零尺寸等诊断），不依赖任何外部服务。

设计上模型「逐页闭环」：write_file 落一页 DSL → slide_upsert 编译插入 →
slide_lint 校验 → 有 error 就改 DSL 重试。编译器在渲染端保证 Flexbox
布局、圆角卡片、字重层级等专业视觉，比手写 python-pptx 脚本稳定得多。

CLI 输出是「若干噪音行 + 最后一行 JSON」，这里统一解析成 dict。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from shutil import which

_TIMEOUT = 120


def find_slidep() -> Path | None:
    """定位 slidep CLI。环境变量 SLIDEP_BIN 优先，其次 WorkBuddy 默认安装位。"""
    env = (os.environ.get("SLIDEP_BIN") or "").strip()
    if env and Path(env).is_file():
        return Path(env)
    home = Path.home()
    candidates = sorted(home.glob(".workbuddy/binaries/node/versions/*/slidep.cmd"))
    if candidates:
        return candidates[-1]  # 多版本时取最新
    posix = which("slidep")
    return Path(posix) if posix else None


def _parse_json_output(stdout: str, stderr: str = "") -> dict:
    """slidep 的 JSON 结果可能落在 stdout 或 stderr（失败时在 stderr），取最后一行可解析的。"""
    for stream in (stdout, stderr):
        for line in reversed(stream.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return {"ok": False, "error": f"无法解析 slidep 输出：{(stdout + stderr)[-400:]}"}


def _exec(exe: Path, args: list[str]) -> tuple[subprocess.CompletedProcess, dict]:
    cmd = ["cmd", "/c", str(exe), *args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=_TIMEOUT,
        encoding="utf-8", errors="replace",
    )
    return proc, _parse_json_output(proc.stdout or "", proc.stderr or "")


def create(pptx: Path) -> dict:
    """创建空白 PPTX（upsert 的前置条件：目标文件必须已存在）。"""
    exe = find_slidep()
    if exe is None:
        return {"ok": False, "error": "未找到 slidep 引擎（SLIDEP_BIN 未设置且默认路径不存在）"}
    proc, result = _exec(exe, ["create", str(pptx)])
    result["exit_code"] = proc.returncode
    return result


def upsert(pptx: Path, dsl: str, page_index: int = -1) -> dict:
    """把一页 DSL 编译后追加（page_index=-1）或替换（>=0）到 PPTX；文件不存在先 create。"""
    exe = find_slidep()
    if exe is None:
        return {"ok": False, "error": "未找到 slidep 引擎（SLIDEP_BIN 未设置且默认路径不存在）"}
    if not pptx.exists():
        created = create(pptx)
        if not created.get("ok"):
            return {"ok": False, "error": f"创建 PPTX 失败：{created.get('error', '未知错误')}"}
    proc, result = _run_dsl(exe, "upsert-dsl", pptx, dsl, page_index)
    result["exit_code"] = proc.returncode
    return result


def lint(pptx: Path, dsl: str, page_index: int = 0) -> dict:
    """本地布局校验：返回 diagnostics（no-overflow / component-overlap 等）。"""
    exe = find_slidep()
    if exe is None:
        return {"ok": False, "error": "未找到 slidep 引擎"}
    proc, result = _run_dsl(exe, "lint", pptx, dsl, page_index)
    result["exit_code"] = proc.returncode
    return result


def _run_dsl(exe: Path, subcommand: str, pptx: Path, dsl: str, page_index: int) -> tuple[subprocess.CompletedProcess, dict]:
    """写 DSL 到临时文件（避开 Windows 8KB 命令行上限），调 CLI，解析结果。"""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".slide", encoding="utf-8", delete=False
    ) as fh:
        fh.write(dsl)
        dsl_file = fh.name
    try:
        return _exec(exe, [subcommand, str(pptx),
                           "--dsl-file", dsl_file, "--page-index", str(page_index)])
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([], 1), {"ok": False, "error": f"slidep {subcommand} 超时（{_TIMEOUT}s）"}
    except Exception as exc:  # noqa: BLE001 — 工具层兜底
        return subprocess.CompletedProcess([], 1), {"ok": False, "error": str(exc)}
    finally:
        try:
            os.unlink(dsl_file)
        except OSError:
            pass  # 清理失败不影响主流程（Windows 上文件常被占用）
