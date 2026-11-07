"""RAG 管道：切片 → 嵌入 → 检索。

检索三种模式：
- vector：纯向量余弦（语义相近）
- lexical：纯 BM25（术语/编号精确匹配）
- hybrid：两路召回后用 RRF 融合，互补长短（默认）

向量召回走 `VectorIndex`（memory: numpy / milvus: HNSW），换后端不改本文件。
纯向量模式只回表命中的切片；hybrid 因为要做 BM25，仍需目标知识库的全量文本。
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings
from app.services.embeddings import embed_texts
from app.services.lexical import BM25
from app.services.store import Chunk, Document, KBStore, get_store
from app.services.vector import MemoryIndex, VectorIndexError, get_vector_index

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_STRATEGY = "recursive"   # recursive | fixed | semantic（按 markdown 标题）

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


@dataclass
class IngestResult:
    document: Document
    deduped: bool = False     # 内容与已有文档完全相同，没有重复建索引
    version: int = 1
    chunk_count: int = 0


def content_digest(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _split(
    content: str, *, strategy: str = DEFAULT_STRATEGY,
    size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """切片策略。

    - recursive：按段落/句子边界递归切，保留语义完整（默认）
    - fixed：定长硬切，适合表格/日志这类无段落结构的文本
    - semantic：先按 markdown 标题切块，过长再递归细分，保住章节边界
    """
    size = max(100, min(int(size), 4000))
    overlap = max(0, min(int(overlap), size // 2))

    if strategy == "fixed":
        step = max(1, size - overlap)
        return [
            content[i:i + size] for i in range(0, len(content), step)
            if content[i:i + size].strip()
        ]

    if strategy == "semantic":
        blocks = re.split(r"\n(?=#{1,3} )", content)
        out: list[str] = []
        for block in blocks:
            if len(block) <= size * 2:
                out.append(block)
            else:
                out.extend(_split(block, strategy="recursive", size=size, overlap=overlap))
        return [b.strip() for b in out if b.strip()] or [content]

    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    return splitter.split_text(content) or [content]


async def _embed_and_store(
    store: KBStore, doc: Document, kb_id: str, content: str, texts: list[str],
) -> int:
    """嵌入并写入切片，返回切片数。切片 id 用 `{doc_id}-{seq}`，删过文档也不会撞号。"""
    vectors, kind = await embed_texts(texts)
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
    return len(chunks)


def _drop_document_index(store: KBStore, doc_id: str) -> None:
    """删掉一篇文档的切片与向量（重切片、删文档都要走）。"""
    store.delete_chunks_of_document(doc_id)
    try:
        get_vector_index().delete_document(doc_id)
    except VectorIndexError:
        pass  # 索引不可用时以数据库为准，重建脚本会补


async def ingest_document(
    store: KBStore, kb_id: str, name: str, content: str,
    *, source: str = "text", content_hash: str = "", dedupe: bool = True,
    note: str = "", strategy: str = DEFAULT_STRATEGY,
    chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> IngestResult:
    """切片 → 嵌入 → 入库，带内容去重与同名版本管理。

    - 内容完全相同（sha256 一致）→ 直接返回已有文档，不重复嵌入
    - 同一知识库内同名文档内容不同 → 生成新版本，旧内容快照进 document_versions
    """
    digest = content_hash or content_digest(content)

    if dedupe:
        same = store.find_document_by_hash(kb_id, digest)
        if same is not None:
            return IngestResult(same, deduped=True, version=same.version,
                                chunk_count=same.chunk_count)

    previous = next((d for d in store.documents_of_kb(kb_id) if d.name == name), None)
    if previous is not None:
        # 留档：把旧内容记进版本表，回滚时用
        store.repo.add_version(
            document_id=previous.id, kb_id=kb_id, version=previous.version,
            content=previous.content, content_hash=previous.content_hash,
            chunk_count=previous.chunk_count, source=previous.source,
            note="被新版本替换",
        )
        _drop_document_index(store, previous.id)
        texts = _split(content, strategy=strategy, size=chunk_size, overlap=chunk_overlap)
        doc = store.update_document(
            previous.id, content=content, content_hash=digest,
            version=previous.version + 1, chunk_count=len(texts), source=source,
        )
        assert doc is not None
        count = await _embed_and_store(store, doc, kb_id, content, texts)
        # 新版本也留档，否则只能回滚到"上一版"，看不到每一版的内容
        store.repo.add_version(
            document_id=doc.id, kb_id=kb_id, version=doc.version, content=content,
            content_hash=digest, chunk_count=count, source=source,
            note=note or "更新入库",
        )
        store.save()
        return IngestResult(doc, version=doc.version, chunk_count=count)

    texts = _split(content, strategy=strategy, size=chunk_size, overlap=chunk_overlap)
    doc = store.add_document(kb_id, name, content, chunk_count=len(texts),
                             source=source, content_hash=digest)
    store.repo.add_version(
        document_id=doc.id, kb_id=kb_id, version=doc.version, content=content,
        content_hash=digest, chunk_count=len(texts), source=source, note="首次入库",
    )
    count = await _embed_and_store(store, doc, kb_id, content, texts)
    store.save()
    return IngestResult(doc, version=doc.version, chunk_count=count)


async def reindex_document(store: KBStore, doc_id: str) -> int:
    """按当前切片策略重建一篇文档的切片与向量（版本号不变）。"""
    doc = store.get_document(doc_id)
    if doc is None:
        raise ValueError(f"文档不存在：{doc_id}")
    _drop_document_index(store, doc_id)
    texts = _split(doc.content)
    store.update_document(doc_id, chunk_count=len(texts))
    doc = store.get_document(doc_id)
    assert doc is not None
    count = await _embed_and_store(store, doc, doc.kb_id, doc.content, texts)
    store.save()
    return count


async def reembed_chunk(store: KBStore, chunk_id: str, text: str) -> Chunk | None:
    """改一条切片的文本并重算向量（前端切片编辑器用）。"""
    found = store.get_chunks([chunk_id])
    if not found:
        return None
    vectors, kind = await embed_texts([text])
    store.update_chunk(chunk_id, text=text, vector=vectors[0], embedding_kind=kind)
    store.save()
    try:
        get_vector_index().upsert(store.get_chunks([chunk_id]))
    except VectorIndexError:
        pass
    rows = store.get_chunks([chunk_id])
    return rows[0] if rows else None


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
