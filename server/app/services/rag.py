"""RAG 管道：切片 → 嵌入 → 检索。

检索三种模式：
- vector：纯向量余弦（语义相近）
- lexical：纯 BM25（术语/编号精确匹配）
- hybrid：两路召回后用 RRF 融合，互补长短（默认）

向量召回走 `VectorIndex`（memory: numpy / milvus: HNSW），换后端不改本文件。
纯向量模式只回表命中的切片；hybrid 因为要做 BM25，仍需目标知识库的全量文本。
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
from app.services.vector import MemoryIndex, VectorIndexError, get_vector_index

_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

RetrievalMode = Literal["vector", "lexical", "hybrid"]

# 单路召回宽度：取 top_k 的若干倍，再融合截断
CANDIDATE_FACTOR = 4
# 余弦低于该值视为无关（嵌入模型对完全不相关文本也会给出 0.2~0.5，靠阈值砍掉噪声）
MIN_SIMILARITY = 0.01


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
    """单对向量余弦（保留给单测与离线核算；热路径走 MemoryIndex 的矩阵实现）。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def ingest_document(
    store: KBStore, kb_id: str, name: str, content: str,
    *, source: str = "text", content_hash: str = "",
) -> Document:
    """切片 → 嵌入 → 入库。

    切片 id 用 `{doc_id}-{seq}` 而不是全局计数：v0.1 用 `len(store.chunks)` 生成，
    删过文档后编号会撞车，是隐性数据损坏。
    """
    texts = _splitter.split_text(content)
    if not texts:
        texts = [content]
    vectors, kind = await embed_texts(texts)

    doc = store.add_document(kb_id, name, content, chunk_count=len(texts),
                             source=source, content_hash=content_hash)

    chunks: list[Chunk] = []
    cursor = 0
    for seq, (text, vec) in enumerate(zip(texts, vectors, strict=True)):
        start = content.find(text, cursor)
        if start < 0:
            start = cursor
        end = start + len(text)
        cursor = end
        chunks.append(
            Chunk(
                id=f"{doc.id}-{seq}", kb_id=kb_id, document_id=doc.id, text=text,
                vector=vec, embedding_kind=kind, seq=seq,
                char_start=start, char_end=end, created_at=now_iso(),
            )
        )
    store.add_chunks(chunks)
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
    width = max(top_k * CANDIDATE_FACTOR, top_k)
    index = get_vector_index()

    qvec: list[float] | None = None
    if mode in ("vector", "hybrid"):
        embedded, _ = await embed_texts([query])
        qvec = embedded[0]

    vec_hits: list[tuple[str, float]] = []
    if mode == "vector":
        # 纯向量：只把命中的切片回表，不必把整库向量读进进程
        assert qvec is not None
        try:
            vec_hits = index.search(kb_ids, qvec, width)
            candidates = store.get_chunks([cid for cid, _ in vec_hits])
        except VectorIndexError as exc:
            # 索引故障降级为内存计算：宁可慢，不可检索整体 500
            print(f"[rag] 向量索引不可用，降级内存打散：{exc}", flush=True)
            candidates = store.list_chunks(kb_ids)
            vec_hits = MemoryIndex.score(candidates, qvec, width)
        if not candidates:
            return []
    else:
        # 词法/混合：BM25 必须在目标库全量文本上建索引
        candidates = store.list_chunks(kb_ids)
        if not candidates:
            return []
        if qvec is not None:
            try:
                vec_hits = index.search(kb_ids, qvec, width, chunks=candidates)
            except VectorIndexError as exc:
                print(f"[rag] 向量索引不可用，降级内存打散：{exc}", flush=True)
                vec_hits = MemoryIndex.score(candidates, qvec, width)

    by_id = {c.id: c for c in candidates}
    # 索引里可能残留已删切片，统一用回表结果兜底
    vec_ranked = [(cid, s) for cid, s in vec_hits if s > MIN_SIMILARITY and cid in by_id]
    lex_ranked: list[tuple[str, float]] = []

    if mode in ("lexical", "hybrid"):
        bm25 = BM25([(c.id, c.text) for c in candidates])
        lex_ranked = [(cid, s) for cid, s in bm25.search(query, width) if cid in by_id]

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

    doc_names = store.doc_names()
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
