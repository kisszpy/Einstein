"""浏览器能力（Pro）：像人一样"看"一个页面，而不是只抓一段正文。

和 `web.py` 的分工：

- `web.py` 是检索链路上的轻量读取（httpx + 正则抽正文），够快但只给文本。
- 这里是智能体侧的"浏览器"：打开页面拿到标题 / 正文 / 链接清单，需要时用
  Playwright 跑真·无头浏览器截全页图（能渲染 JS、能落盘成产物）。

Playwright 不是硬依赖：没安装时 `browser_shot` 返回明确提示，`browser_open` /
`browser_links` 仍然可用（HTTP 抓取 + 解析）。装了 Playwright 就自动升级，
不需要改代码。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from app.services.capabilities import web

MAX_LINKS = 80


class BrowserError(Exception):
    pass


class _LinkParser(HTMLParser):
    """抽 <a href> 的可见文字；顺手把 <title> 也拿出来。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._in_a = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            return
        self._href = href
        self._text = []
        self._in_a = True

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_a:
            return
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        if self._href:
            self.links.append((text, self._href))
        self._in_a = False
        self._href = ""
        self._text = []


def parse_links(html: str, base_url: str) -> list[dict]:
    """把 HTML 里的链接解析成 [{text, url}]，去掉纯锚点与重复项。"""
    parser = _LinkParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 — 畸形 HTML 不该让整次浏览失败
        pass
    seen: set[str] = set()
    rows: list[dict] = []
    for text, href in parser.links:
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        rows.append({"text": text or url.rsplit("/", 1)[-1] or url, "url": url})
        if len(rows) >= MAX_LINKS:
            break
    return rows


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


# ---- Node 兜底：仓库 web/ 里已经装了 playwright-core 与 chromium ----

# 仓库根：server/app/services/capabilities/x.py → 上 5 层
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHOT_SCRIPT = Path(__file__).with_name("shot_url.mjs")


def _node_modules() -> Path | None:
    for candidate in (
        _REPO_ROOT / "web" / "node_modules",
        Path.cwd() / "web" / "node_modules",
    ):
        if (candidate / "playwright-core").is_dir():
            return candidate
    return None


def _chrome_path() -> str:
    """找本机 ms-playwright 里的 chromium（Node 兜底用；找不到就让 playwright
    自己按默认路径找）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or ""
    if not base:
        return ""
    root = Path(base) / "ms-playwright"
    if not root.is_dir():
        return ""
    for folder in sorted(root.glob("chromium-*"), reverse=True):
        for rel in ("chrome-win64/chrome.exe", "chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
            exe = folder / rel
            if exe.is_file():
                return str(exe)
    return ""


def node_available() -> bool:
    return bool(shutil.which("node") and _node_modules() and _SHOT_SCRIPT.is_file())


def open_page(url: str, max_chars: int = 6000) -> dict:
    """打开页面：标题 + 正文 + 链接清单，一次抓取给全，省得模型反复请求。"""
    raw, final_url, ctype = web.fetch_raw(url)
    page: dict = {"title": "", "text": "", "chars": 0, "kind": "article"}
    html = ""
    if ctype and not any(x in ctype for x in ("html", "text", "json", "xml")):
        page["kind"] = "binary"
    else:
        html = web.decode_body(raw, ctype)
        if "json" in ctype:
            page.update({"kind": "json", "text": html[:max_chars], "chars": len(html),
                         "title": final_url})
        else:
            # _extract 是本包内 web.py 的正文抽取（去脚本/导航/页脚），
            # 与 read_page 用的是同一套规则，这里直接复用避免二次抓取
            title, text = web._extract(html, final_url)
            page.update({"title": title, "text": text[:max_chars], "chars": len(text)})

    links = parse_links(html, final_url) if html else []
    return {
        "url": url,
        "final_url": final_url,
        "title": page.get("title") or final_url,
        "kind": page.get("kind", "article"),
        "chars": page.get("chars", 0),
        "text": page.get("text", ""),
        "links": links[:40],
        "links_total": len(links),
    }


def links_of(url: str, contains: str = "", limit: int = 40) -> dict:
    raw, final_url, ctype = web.fetch_raw(url)
    if ctype and "html" not in ctype:
        raise BrowserError(f"该地址不是 HTML（{ctype}），无法解析链接")
    html = web.decode_body(raw, ctype)
    rows = parse_links(html, final_url)
    if contains:
        needle = contains.lower()
        rows = [r for r in rows if needle in r["text"].lower() or needle in r["url"].lower()]
    limit = max(1, min(int(limit or 40), MAX_LINKS))
    return {"url": url, "final_url": final_url, "total": len(rows), "links": rows[:limit]}


def _shot_python(
    url: str,
    target: Path,
    *,
    full_page: bool,
    width: int,
    timeout_ms: int,
) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": int(width), "height": 900})
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            title = page.title()
            page.screenshot(path=str(target), full_page=full_page)
            final_url = page.url
        finally:
            browser.close()
    return {"title": title, "final_url": final_url}


def _shot_node(
    url: str,
    target: Path,
    *,
    full_page: bool,
    width: int,
    timeout_s: int,
) -> dict:
    """走仓库自带的 playwright-core（Node）截图。"""
    node = shutil.which("node")
    modules = _node_modules()
    if not node or not modules:
        raise BrowserError("没有可用的 Node/playwright-core")
    env = dict(os.environ)
    env["EINSTEIN_NODE_MODULES"] = str(modules)
    chrome = _chrome_path()
    if chrome:
        env["EINSTEIN_CHROME_PATH"] = chrome
    proc = subprocess.run(
        [node, str(_SHOT_SCRIPT), url, str(target), "1" if full_page else "0", str(width)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        env=env,
    )
    if proc.returncode != 0:
        raise BrowserError((proc.stderr or proc.stdout or "Node 截图失败").strip()[:300])
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise BrowserError(f"截图输出无法解析：{(proc.stdout or '')[:200]}") from exc
    return {"title": data.get("title") or url, "final_url": data.get("final_url") or url}


def screenshot(
    url: str,
    target: Path,
    *,
    full_page: bool = True,
    width: int = 1280,
    height: int = 900,
    timeout_ms: int = 45_000,
) -> dict:
    """截图落盘。优先 Python Playwright，其次仓库自带的 Node playwright-core。"""
    target.parent.mkdir(parents=True, exist_ok=True)

    if playwright_available():
        info = _shot_python(url, target, full_page=full_page, width=width, timeout_ms=timeout_ms)
    elif node_available():
        info = _shot_node(
            url, target,
            full_page=full_page,
            width=width,
            timeout_s=max(10, min(timeout_ms // 1000 + 10, 120)),
        )
    else:
        raise BrowserError(
            "当前环境没有可用的无头浏览器，无法截图（browser_open / browser_links 仍可用）。"
            "两种启用方式：① pip install playwright && playwright install chromium；"
            "② 在 web/ 下 npm install（仓库已用 playwright-core，后端会自动发现）"
        )

    return {
        "url": url,
        "final_url": info.get("final_url", url),
        "title": info.get("title", ""),
        "path": str(target),
        "bytes": target.stat().st_size if target.exists() else 0,
        "full_page": full_page,
        "engine": "python" if playwright_available() else "node",
    }


def capabilities() -> dict:
    """能力自述：前端/诊断脚本用它解释为什么截图不可用。"""
    return {
        "http": True,
        "playwright": playwright_available(),
        "node_playwright": node_available(),
        "screenshot": playwright_available() or node_available(),
        "tools": ["browser_open", "browser_links", "browser_shot"],
    }
