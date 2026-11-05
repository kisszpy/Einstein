"""代码执行能力：在项目目录内跑一小段脚本，把输出回给模型。

不是安全沙箱（没有容器/权限隔离），所以边界靠三条：工作目录锁定在
项目内、进程级超时强杀、环境变量只透传必要项（不把服务端密钥带给
生成代码）。配置项 `agent_code_exec` 可整体关闭这条能力。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.services.capabilities import sandbox

MAX_TIMEOUT = 120
DEFAULT_TIMEOUT = 60
MAX_OUTPUT = 8000

_KEEP_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "COMSPEC", "HOME", "USERPROFILE", "LANG")


class CodeError(Exception):
    pass


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _KEEP_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_COLOR"] = "1"
    return env


def _clip(text: str, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n…（中间省略）…\n" + text[-half:], True


def run_code(
    workspace_id: str,
    project: str,
    code: str = "",
    path: str = "",
    language: str = "python",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    root = sandbox.project_dir(workspace_id, project)
    lang = (language or "python").lower()
    if lang in ("py", "python3"):
        lang = "python"
    if lang in ("js", "javascript", "node"):
        lang = "node"
    if lang not in ("python", "node"):
        raise CodeError("只支持 python 与 node")

    secs = max(5, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    tmp_dir = root / ".agent_tmp"
    tmp_dir.mkdir(exist_ok=True)

    if path:
        target = sandbox.resolve_in(root, path)
        if not target.is_file():
            raise CodeError(f"文件不存在：{path}")
        script = target
        cleanup = False
    else:
        if not (code or "").strip():
            raise CodeError("code 与 path 至少要给一个")
        suffix = ".py" if lang == "python" else ".mjs"
        fd, tmp = tempfile.mkstemp(dir=tmp_dir, suffix=suffix)
        os.close(fd)
        script = Path(tmp)
        script.write_text(code, encoding="utf-8")
        cleanup = True

    if lang == "python":
        cmd = [sys.executable, "-X", "utf8", str(script)]
    else:
        node = shutil.which("node")
        if not node:
            raise CodeError("环境中没有 node，无法执行 JavaScript")
        cmd = [node, str(script)]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=secs,
            env=_clean_env(),
        )
        out, cut_out = _clip(proc.stdout or "")
        err, cut_err = _clip(proc.stderr or "")
        return {
            "language": lang,
            "script": path or f".agent_tmp/{script.name}",
            "exit_code": proc.returncode,
            "ok": proc.returncode == 0,
            "stdout": out,
            "stderr": err,
            "output_truncated": cut_out or cut_err,
        }
    except subprocess.TimeoutExpired:
        return {
            "language": lang,
            "script": path or f".agent_tmp/{script.name}",
            "exit_code": -1,
            "ok": False,
            "stdout": "",
            "stderr": f"执行超时（{secs}s）已被终止。检查是否有死循环或等待输入。",
            "output_truncated": False,
        }
    finally:
        if cleanup:
            try:
                script.unlink(missing_ok=True)
            except OSError:
                pass
