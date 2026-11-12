"""无头浏览器实测层：把 HTML 交给 Chromium，拿回逐行几何 + 截图。

复用 capabilities.browser_pro 的 Node 兜底发现逻辑（web/node_modules 里的
playwright-core + 本机 ms-playwright chromium），所以不需要额外 pip 安装。
Python 版 playwright 装了也不走它——抽取脚本是 JS，统一走 Node 一条路径，
避免两套引擎测出不同行宽。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from app.core.logging import get_logger
from app.services.capabilities import browser_pro
from app.services.ppt_studio.htmlkit.compose import SlideHtml
from app.services.ppt_studio.htmlkit.tokens import CANVAS_H, CANVAS_W

logger = get_logger(__name__)

_SCRIPT = Path(__file__).with_name("measure_slides.mjs")
_TIMEOUT_S = 180
# 设计台每拧一次旋钮就起一个 Chromium。不设闸门的话，多人同时拖滑块能把机器
# 拖垮（每个实例 ~200MB）。两个并发够交互用，排不上的直接失败让前端保留旧预览。
_SLOTS = threading.BoundedSemaphore(2)
_SLOT_WAIT_S = 45


class MeasureError(Exception):
    """浏览器不可用或渲染失败。调用方据此回落 DesignRenderer。"""


def available() -> bool:
    return bool(shutil.which("node") and browser_pro._node_modules() and _SCRIPT.is_file())


def unavailable_reason() -> str:
    if not shutil.which("node"):
        return "没有找到 node 可执行文件"
    if not browser_pro._node_modules():
        return "web/node_modules 里没有 playwright-core（在 web/ 下执行 npm install 即可）"
    if not _SCRIPT.is_file():
        return f"缺少渲染脚本 {_SCRIPT.name}"
    return ""


def measure(slides: list[SlideHtml], out_dir: Path, *, screenshot: bool = True,
            canvas: tuple[int, int] = (CANVAS_W, CANVAS_H)) -> list[dict]:
    """渲染并实测。返回 [{index, nodes, diagnostics, size, screenshot}]。"""
    if not slides:
        return []
    if not available():
        raise MeasureError(f"无头浏览器不可用：{unavailable_reason()}")
    modules = browser_pro._node_modules()
    out_dir.mkdir(parents=True, exist_ok=True)
    job_path = out_dir / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "slides": [{"index": s.index, "html": s.html} for s in slides],
                "outDir": str(out_dir),
                "width": canvas[0],
                "height": canvas[1],
                "screenshot": screenshot,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["EINSTEIN_NODE_MODULES"] = str(modules)
    chrome = browser_pro._chrome_path()
    if chrome:
        env["EINSTEIN_CHROME_PATH"] = chrome
    try:
        proc = _run(job_path, env)
    except subprocess.TimeoutExpired as exc:
        raise MeasureError(f"浏览器渲染超时（>{_TIMEOUT_S}s）") from exc
    summary: dict = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if proc.returncode != 0 or not summary.get("ok"):
        detail = summary.get("error") or (proc.stderr or proc.stdout or "")[-400:]
        raise MeasureError(f"浏览器渲染失败：{detail}")
    layout_path = out_dir / "layout.json"
    if not layout_path.is_file():
        raise MeasureError("渲染完成但没有产出 layout.json")
    data = json.loads(layout_path.read_text(encoding="utf-8"))
    results = data.get("slides") or []
    logger.info("HTML 实测完成：%d 页，输出 %s", len(results), out_dir)
    return results


def _run(job_path: Path, env: dict) -> subprocess.CompletedProcess:
    if not _SLOTS.acquire(timeout=_SLOT_WAIT_S):
        raise MeasureError(f"排版引擎繁忙（等待 >{_SLOT_WAIT_S}s），请稍后重试")
    try:
        return subprocess.run(
            [shutil.which("node"), str(_SCRIPT), str(job_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_TIMEOUT_S, env=env,
        )
    finally:
        _SLOTS.release()


def thumbnail(png_path: Path, width: int = 480) -> bytes:
    """PNG → JPEG 缩略图字节。确认卡一次要 10+ 张，JPEG 比 PNG 小一个量级。"""
    from PIL import Image

    with Image.open(png_path) as image:
        rgb = image.convert("RGB")
        ratio = width / max(1, rgb.width)
        size = (width, max(1, round(rgb.height * ratio)))
        resized = rgb.resize(size, Image.LANCZOS)
        from io import BytesIO

        buffer = BytesIO()
        resized.save(buffer, format="JPEG", quality=82, optimize=True)
        return buffer.getvalue()