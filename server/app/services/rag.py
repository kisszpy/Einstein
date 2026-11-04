"""RAG 管道：切片 → 嵌入 → 检索。

M0 用余弦相似度暴力检索（内存适配器），数据量上来后换 Milvus（HNSW）+ BM25 混合。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.embeddings import embed_texts
from app.services.store import Chunk, Document, KBStore, get_store

_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float


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


async def search(store: KBStore, kb_ids: list[str], query: str, top_k: int = 4) -> list[RetrievedChunk]:
    """向量检索：query 嵌入后在指定知识库范围内取 top-k。"""
    if not kb_ids:
        return []
    (qvec), _ = await embed_texts([query])
    q = qvec[0]

    candidates = [c for c in store.chunks if c.kb_id in kb_ids]
    # 防御：维度不匹配的历史数据不参与打分（避免 zip strict 崩溃）
    candidates = [c for c in candidates if len(c.vector) == len(q)]
    scored = [(c, _cosine(q, c.vector)) for c in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    doc_names = {d.id: d.name for d in store.documents.values()}
    return [
        RetrievedChunk(
            chunk_id=c.id,
            document_id=c.document_id,
            document_name=doc_names.get(c.document_id, "未知文档"),
            text=c.text,
            score=round(s, 4),
        )
        for c, s in scored[:top_k]
        if s > 0.01  # 过滤完全不相关的 mock 噪声
    ]


def build_context_block(hits: list[RetrievedChunk]) -> str:
    """检索结果 → 注入提示词的上下文块。"""
    parts = [f"[{i + 1}] 来源：{h.document_name}\n{h.text}" for i, h in enumerate(hits)]
    return "\n\n".join(parts)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
