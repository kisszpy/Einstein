"""源同步：重抓判定与过期提示。

两条最容易做错的边界：
- **内容没变必须不重嵌入**：否则每天为同一份内容付一次嵌入的钱；
- **只有"有抓取地址"的文档才判过期**：手工粘贴的文本不会自己变旧，
  把它标红会让用户学会无视这个标记。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Document
from app.services import sources
from app.services.rag import content_digest


class _Store:
    """最小替身：只实现 refresh 用到的两个动作。"""

    def __init__(self) -> None:
        self.updated: dict | None = None
        self.saved = False

    def update_document(self, doc_id: str, **fields):  # noqa: ANN003
        self.updated = {"doc_id": doc_id, **fields}
        return None

    def save(self) -> None:
        self.saved = True


def _doc(content: str = "原始正文", *, url: str = "https://e.com/a", days_ago: int = 1) -> Document:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return Document(
        id="d1", kb_id="kb1", name="制度",
        content=content, content_hash=content_digest(content),
        source="url", meta={"url": url, "fetched_at": stamp},
    )


def test_manual_document_never_goes_stale():
    doc = Document(id="d2", kb_id="kb1", name="手写", content="x", source="text", meta={})
    assert sources.is_stale(doc) is False
    assert sources.describe(doc)["stale"] is False


def test_url_document_goes_stale_after_threshold():
    assert sources.is_stale(_doc(days_ago=1), days=30) is False
    assert sources.is_stale(_doc(days_ago=45), days=30) is True


def test_missing_timestamp_counts_as_stale():
    doc = _doc()
    doc.meta = {"url": "https://e.com/a"}
    assert sources.is_stale(doc) is True


async def test_refresh_unchanged_only_touches_timestamp(monkeypatch):
    doc = _doc()
    store = _Store()
    monkeypatch.setattr(sources, "fetch_url", lambda url: ("制度", doc.content))

    called = {"n": 0}

    async def _boom(*a, **kw):  # noqa: ANN002
        called["n"] += 1
        raise AssertionError("内容没变还调用了 ingest_document，等于白花一次嵌入")

    monkeypatch.setattr(sources, "ingest_document", _boom)

    result = await sources.refresh(store, "kb1", doc)
    assert result["changed"] is False
    assert result["error"] == ""
    assert called["n"] == 0
    assert store.saved is True
    assert store.updated["meta"]["url"] == "https://e.com/a"
    assert store.updated["meta"]["fetched_at"]


async def test_refresh_changed_ingests_new_version(monkeypatch):
    doc = _doc()
    store = _Store()
    monkeypatch.setattr(sources, "fetch_url", lambda url: ("制度", "改过的正文"))

    seen: dict = {}

    class _Result:
        version = 2
        chunk_count = 7

    async def _fake_ingest(store_, kb_id, name, content, **kw):  # noqa: ANN001
        seen.update({"kb": kb_id, "name": name, "content": content, **kw})
        return _Result()

    monkeypatch.setattr(sources, "ingest_document", _fake_ingest)

    result = await sources.refresh(store, "kb1", doc)
    assert result["changed"] is True
    assert result["version"] == 2
    assert result["chunk_count"] == 7
    # 同名入库才会走"新版本 + 旧内容留档"那条路
    assert seen["name"] == "制度"
    assert seen["meta"]["url"] == "https://e.com/a"
    assert seen["note"] == "源同步更新"


async def test_refresh_rejects_non_url_document():
    doc = Document(id="d3", kb_id="kb1", name="手写", content="x", source="text", meta={})
    result = await sources.refresh(_Store(), "kb1", doc)
    assert result["changed"] is False
    assert "不是从链接导入" in result["error"]


async def test_refresh_reports_fetch_error_without_raising(monkeypatch):
    from app.services.parsers import ParseError

    def _fail(url: str):
        raise ParseError("https://e.com/a: 抓取失败（404）")

    monkeypatch.setattr(sources, "fetch_url", _fail)
    result = await sources.refresh(_Store(), "kb1", _doc())
    assert result["changed"] is False
    assert "404" in result["error"]


def test_describe_exposes_url_and_freshness():
    info = sources.describe(_doc(days_ago=90))
    assert info["source_url"] == "https://e.com/a"
    assert info["stale"] is True
    assert info["fetched_at"]
