"""BM25 整库倒排表缓存 + 切片签名。

为什么值得单独测：缓存一旦判错，用户会看到"明明有资料却检索不到"，
而且只在"刚改完切片"这种时刻出现，最容易在演示时踩到。所以断言集中在
三件事：签名变了必须重建、TTL 到了必须重建、显式作废必须立刻生效。
"""

from __future__ import annotations

import pytest

from app.services import lexical


@pytest.fixture(autouse=True)
def _clean():
    lexical.invalidate()
    yield
    lexical.invalidate()


def _loader(calls: list[int], docs: list[tuple[str, str]]):
    def load():
        calls.append(1)
        return docs
    return load


def test_same_signature_reuses_index():
    calls: list[int] = []
    docs = [("c1", "死锁是互相等待"), ("c2", "生成器用 yield 产出")]
    first = lexical.get_index(["kb1"], (2, "2026-11-09T00:00:00"), _loader(calls, docs))
    second = lexical.get_index(["kb1"], (2, "2026-11-09T00:00:00"), _loader(calls, docs))
    assert first is second
    assert len(calls) == 1, "签名没变就不该重建"


def test_signature_change_rebuilds():
    calls: list[int] = []
    docs = [("c1", "死锁是互相等待")]
    lexical.get_index(["kb1"], (1, "t1"), _loader(calls, docs))
    lexical.get_index(["kb1"], (2, "t2"), _loader(calls, docs))
    assert len(calls) == 2


def test_kb_set_order_does_not_matter():
    calls: list[int] = []
    docs = [("c1", "死锁")]
    lexical.get_index(["kb2", "kb1"], (1, "t"), _loader(calls, docs))
    lexical.get_index(["kb1", "kb2"], (1, "t"), _loader(calls, docs))
    assert len(calls) == 1


def test_invalidate_by_kb_only_drops_related_sets():
    calls: list[int] = []
    docs = [("c1", "死锁")]
    lexical.get_index(["kb1"], (1, "t"), _loader(calls, docs))
    lexical.get_index(["kb2"], (1, "t"), _loader(calls, docs))
    assert len(calls) == 2
    lexical.invalidate("kb1")
    lexical.get_index(["kb2"], (1, "t"), _loader(calls, docs))   # 命中
    lexical.get_index(["kb1"], (1, "t"), _loader(calls, docs))   # 重建
    assert len(calls) == 3


def test_ttl_expiry_forces_rebuild(monkeypatch):
    calls: list[int] = []
    docs = [("c1", "死锁")]
    lexical.get_index(["kb1"], (1, "t"), _loader(calls, docs))
    monkeypatch.setattr(lexical, "_INDEX_TTL", 0.0)
    lexical.get_index(["kb1"], (1, "t"), _loader(calls, docs))
    assert len(calls) == 2


def test_cached_index_still_answers():
    docs = [("c1", "死锁四个必要条件"), ("c2", "无关内容")]
    index = lexical.get_index(["kb1"], (2, "t"), lambda: docs)
    hits = index.search("死锁", 2)
    assert hits and hits[0][0] == "c1"


def test_stats_reports_cache_size():
    lexical.get_index(["kb1"], (1, "t"), lambda: [("c1", "死锁")])
    assert lexical.stats()["indexes"] == 1
