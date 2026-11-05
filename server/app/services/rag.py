"""RAG 管道：切片 → 嵌入 → 检索。

检索三种模式：
- vector：纯向量余弦（语义相近）
- lexical：纯 BM25（术语/编号精确匹配）
- hybrid：两路召回后用 RRF 融合，互补长短（默认）

开发期暴力检索（内存），数据量上来后换 Milvus（HNSW + 全文检索），
接口保持不变。融合参数见 Settings.retrieval_mode / rrf_k。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings
from app.services.embeddings import embed_texts
from app.services.lexical import BM25
from app.services.store import Chunk, Document, KBStore, get_store

_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

RetrievalMode = Literal["vector", "lexical", "hybrid"]

# 单路召回宽度：取 top_k 的若干倍，再融合截断
CANDIDATE_FACTOR = 4


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float                      # 当前模式下的排序分（hybrid 为 RRF 分）
    vector_score: float | None = None  # 余弦相似度（词法模式下为 None）
    lexical_score: float | None = None  # BM25 分（向量模式下为 None）


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def ingest_document(store: KBStore, kb_id: str, name: str, content: str) -> Document:
    """切片 → 嵌入 → 入库。"""
    texts = _splitter.split_text(content)
    if not texts:
        texts = [content]
    vectors, kind = await embed_texts(texts)

    doc = store.add_document(kb_id, name, content, chunk_count=len(texts))
    for text, vec in zip(texts, vectors, strict=True):
        store.add_chunk(
            Chunk(
                id=f"{doc.id}-{len(store.chunks)}",
                kb_id=kb_id,
                document_id=doc.id,
                text=text,
                vector=vec,
                embedding_kind=kind,
            )
        )
    store.save()
    return doc


async def search(
    store: KBStore,
    kb_ids: list[str],
    query: str,
    top_k: int = 4,
    mode: RetrievalMode | None = None,
) -> list[RetrievedChunk]:
    """检索：mode 为空时用 Settings.retrieval_mode（默认 hybrid）。"""
    if not kb_ids:
        return []
    settings = get_settings()
    mode = mode or settings.retrieval_mode  # type: ignore[assignment]
    candidates = [c for c in store.chunks if c.kb_id in kb_ids]
    if not candidates:
        return []

    width = max(top_k * CANDIDATE_FACTOR, top_k)

    vec_ranked: list[tuple[str, float]] = []
    lex_ranked: list[tuple[str, float]] = []

    if mode in ("vector", "hybrid"):
        (qvec), _ = await embed_texts([query])
        q = qvec[0]
        # 防御：维度不匹配的历史数据不参与打分（避免 zip strict 崩溃）
        comparable = [c for c in candidates if len(c.vector) == len(q)]
        vec_scored = [(c.id, _cosine(q, c.vector)) for c in comparable]
        vec_scored.sort(key=lambda pair: pair[1], reverse=True)
        vec_ranked = [(cid, s) for cid, s in vec_scored[:width] if s > 0.01]

    if mode in ("lexical", "hybrid"):
        by_id = {c.id: c for c in candidates}
        index = BM25([(c.id, c.text) for c in candidates])
        lex_ranked = index.search(query, width)
        lex_ranked = [(cid, s) for cid, s in lex_ranked if cid in by_id]

    vec_scores = dict(vec_ranked)
    lex_scores = dict(lex_ranked)

    if mode == "hybrid":
        # RRF：分数与两路量纲无关，只看排名，k 越大头部权重越平缓
        k = settings.rrf_k
        fused: dict[str, float] = {}
        for rank, (cid, _s) in enumerate(vec_ranked):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
        for rank, (cid, _s) in enumerate(lex_ranked):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    elif mode == "vector":
        ordered = vec_ranked[:top_k]
    else:
        ordered = lex_ranked[:top_k]

    by_id = {c.id: c for c in candidates}
    doc_names = {d.id: d.name for d in store.documents.values()}
    hits: list[RetrievedChunk] = []
    for cid, score in ordered:
        c = by_id.get(cid)
        if c is None:
            continue
        hits.append(
            RetrievedChunk(
                chunk_id=c.id,
                document_id=c.document_id,
                document_name=doc_names.get(c.document_id, "未知文档"),
                text=c.text,
                score=round(score, 6 if mode == "hybrid" else 4),
                vector_score=round(vec_scores[cid], 4) if cid in vec_scores else None,
                lexical_score=round(lex_scores[cid], 4) if cid in lex_scores else None,
            )
        )
    return hits


def build_context_block(hits: list[RetrievedChunk]) -> str:
    """检索结果 → 注入提示词的上下文块。"""
    parts = [f"[{i + 1}] 来源：{h.document_name}\n{h.text}" for i, h in enumerate(hits)]
    return "\n\n".join(parts)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
