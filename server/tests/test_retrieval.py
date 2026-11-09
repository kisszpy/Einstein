"""检索门限、文档配额、嵌入指纹。

这三项共同决定「知识库未命中」这句话是否可信：门限太松 → 噪音当证据；
没有配额 → 一篇文档占满名额；指纹不符 → 余弦排序等于随机却没人知道。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.rag import (
    RetrievedChunk,
    apply_score_gate,
    cap_per_document,
    check_embedding_fingerprint,
)


def hit(cid: str, doc: str, *, score: float = 1.0, vector: float | None = None,
        rerank: float | None = None, text: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid, document_id=doc, document_name=doc, text=text,
        score=score, vector_score=vector, rerank_score=rerank,
    )


# ---------------------------------------------------------------- 门限


def test_gate_drops_low_vector_scores():
    hits = [hit("a", "d1", vector=0.12), hit("b", "d2", vector=0.9)]
    kept = apply_score_gate("随便问问", hits, min_score=0.35)
    assert [h.chunk_id for h in kept] == ["b"]


def test_gate_all_low_means_missed():
    """这就是「知识库未命中」分支可达的证据：全低分必须返回空。"""
    hits = [hit("a", "d1", vector=0.2), hit("b", "d2", vector=0.31)]
    assert apply_score_gate("库外问题", hits, min_score=0.35) == []


def test_gate_prefers_rerank_score_over_vector():
    """有重排分时按重排分判：重排是绝对相关度，余弦在同模型下才可比。"""
    hits = [
        hit("a", "d1", vector=0.95, rerank=0.1),   # 余弦高但重排说不相关
        hit("b", "d2", vector=0.4, rerank=0.8),
    ]
    kept = apply_score_gate("问题", hits, min_score=0.35)
    assert [h.chunk_id for h in kept] == ["b"]


def test_gate_lexical_fallback_uses_term_coverage():
    """纯词法命中没有分数：词覆盖率达到一半才放行。"""
    on_topic = hit("a", "d1", text="向量检索的召回率取决于切片粒度")
    off_topic = hit("b", "d2", text="今天天气不错适合出门散步")
    kept = apply_score_gate("向量检索 切片", [on_topic, off_topic], min_score=0.35)
    assert [h.chunk_id for h in kept] == ["a"]


def test_gate_disabled_by_zero_threshold():
    hits = [hit("a", "d1", vector=0.01)]
    assert apply_score_gate("x", hits, min_score=0) == hits


# ---------------------------------------------------------------- 文档配额


def test_cap_per_document_limits_same_doc():
    hits = [hit("a", "d1"), hit("b", "d1"), hit("c", "d1"), hit("d", "d2")]
    kept = cap_per_document(hits, max_per_doc=2)
    assert [h.chunk_id for h in kept] == ["a", "b", "d"]


def test_cap_keeps_cross_document_evidence():
    """一篇结构重复的文档不能占满全部名额——跨文档对比类问题靠这个。"""
    hits = [hit(f"d1-{i}", "d1") for i in range(4)] + [hit("d2-1", "d2")]
    kept = cap_per_document(hits, max_per_doc=2)
    assert {h.document_id for h in kept} == {"d1", "d2"}


# ---------------------------------------------------------------- 嵌入指纹


@dataclass
class _Chunk:
    embedding_model: str = ""
    embedding_dim: int = 0


class _FakeStore:
    """只实现 list_chunks：指纹校验只需要第一条带指纹的切片。"""

    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks

    def list_chunks(self, kb_ids=None, document_id=None, *, with_vector: bool = True):  # noqa: ANN001
        return self._chunks


def test_fingerprint_mismatch_reports_rebuild_command():
    store = _FakeStore([_Chunk("bge-m3", 1024)])
    problem = check_embedding_fingerprint(store, ["kb-x"], model="qwen3-embedding", dim=4096)
    assert problem is not None
    assert "bge-m3" in problem and "4096" in problem
    assert "rebuild_vector_index" in problem


def test_fingerprint_dimension_mismatch_alone_is_enough():
    """模型名相同但维度变了（换了量化版本）同样不可用。"""
    store = _FakeStore([_Chunk("qwen3-embedding", 1024)])
    assert check_embedding_fingerprint(store, ["kb-y"], model="qwen3-embedding", dim=4096)


def test_fingerprint_match_passes():
    store = _FakeStore([_Chunk("qwen3-embedding", 4096)])
    assert check_embedding_fingerprint(store, ["kb-z"], model="qwen3-embedding", dim=4096) is None


def test_fingerprint_absent_skips_check():
    """迁移前的老数据没有指纹：无从判断，就别误报让用户去重建。"""
    store = _FakeStore([_Chunk()])
    assert check_embedding_fingerprint(store, ["kb-old"], model="any", dim=768) is None


def test_fingerprint_store_error_does_not_break_search():
    class _Broken:
        def list_chunks(self, *a, **kw):  # noqa: ANN002, ANN003
            raise RuntimeError("db down")

    assert check_embedding_fingerprint(_Broken(), ["kb-q"], model="m", dim=8) is None
