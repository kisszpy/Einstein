"""浏览器单例：一次任务里多次截图不该反复冷启动 Chromium。

不启真浏览器（CI 里不一定有），用假的 playwright 模块断言"只 launch 一次"。
这类缓存最容易在重构时被打回原形，而症状只是"慢"，没人会报 bug。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com/final"
        self.closed = False

    def goto(self, url, wait_until=None, timeout=None):  # noqa: ANN001, ANN003
        self.url = url

    def title(self) -> str:
        return "示例页"

    def screenshot(self, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n")

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, counter: dict) -> None:
        self.counter = counter
        self.closed = False

    def new_page(self, **kwargs):  # noqa: ANN003
        self.counter["pages"] += 1
        return _FakePage()

    def close(self) -> None:
        self.closed = True


def _install_fake_playwright(monkeypatch) -> dict:
    counter = {"launch": 0, "pages": 0, "stop": 0}

    class _Chromium:
        @staticmethod
        def launch(args=None):  # noqa: ANN001
            counter["launch"] += 1
            return _FakeBrowser(counter)

    class _Playwright:
        chromium = _Chromium()

        def stop(self) -> None:
            counter["stop"] += 1

    module = SimpleNamespace(
        sync_playwright=lambda: SimpleNamespace(start=lambda: _Playwright()),
    )
    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace(sync_api=module))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return counter


def test_screenshot_reuses_browser(monkeypatch, tmp_path):
    from app.services.capabilities import browser_pro

    counter = _install_fake_playwright(monkeypatch)
    browser_pro.shutdown_browser()

    first = browser_pro.screenshot("https://example.com/a", tmp_path / "a.png")
    second = browser_pro.screenshot("https://example.com/b", tmp_path / "b.png")

    assert first["engine"] == "python"
    assert counter["launch"] == 1, "两次截图应复用同一个浏览器进程"
    assert counter["pages"] == 2
    assert (tmp_path / "a.png").exists() and (tmp_path / "b.png").exists()
    assert browser_pro.browser_stats()["running"] is True

    browser_pro.shutdown_browser()
    assert browser_pro.browser_stats()["running"] is False
    assert counter["stop"] == 1


def test_idle_timeout_recycles_browser(monkeypatch, tmp_path):
    from app.services.capabilities import browser_pro

    counter = _install_fake_playwright(monkeypatch)
    browser_pro.shutdown_browser()
    monkeypatch.setattr(browser_pro, "_idle_seconds", lambda: 0.0)

    browser_pro.screenshot("https://example.com/a", tmp_path / "a.png")
    browser_pro.screenshot("https://example.com/b", tmp_path / "b.png")

    # 空闲阈值为 0：上一次的浏览器已经"过期"，必须重起
    assert counter["launch"] == 2
    assert counter["stop"] >= 1
    browser_pro.shutdown_browser()
