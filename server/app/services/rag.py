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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings
from app.services.embeddings import embed_texts
from app.services.lexical import BM25
from app.services.rerank import rerank_hits
from app.services.store import Chunk, Document, KBStore, get_store
from app.services.vector import MemoryIndex, VectorIndexError, get_vector_index

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_STRATEGY = "recursive"   # recursive | fixed | semantic（按 markdown 标题）| parent-child

# 父子切片参数：孩子片（小、准）用于检索命中，父片（大、全）用于回答上下文
PARENT_SIZE = 1000
CHILD_SIZE = 250
CHILD_OVERLAP = 30

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
    rerank_score: float | None = None   # 重排给的绝对相关分（未重排为 None）


class EmbeddingMismatch(RuntimeError):
    """切片向量与当前嵌入模型不一致：检索结果不可信，必须重建索引。

    为什么单独一个异常类：这不是"没命中"，而是"命中了也不对"。混在空结果里
    会让人以为知识库没内容，白白重传文档；混成 500 又看不出该怎么办。
    """


# 指纹缓存：按知识库集合缓存 5 分钟，避免每次检索都回表扫一遍元数据
_FP_CACHE: dict[tuple[str, ...], tuple[float, str, int]] = {}
_FP_TTL_SECONDS = 300.0


def index_fingerprint(store: KBStore, kb_ids: list[str]) -> tuple[str, int] | None:
    """索引里第一条带指纹的切片 → (模型名, 维度)。

    没有指纹（迁移前入库的老数据）返回 None：无从判断，就别误报。
    """
    key = tuple(sorted(kb_ids))
    now = time.time()
    cached = _FP_CACHE.get(key)
    if cached and (now - cached[0]) < _FP_TTL_SECONDS:
        return (cached[1], cached[2]) if (cached[1] or cached[2]) else None

    found: tuple[str, int] | None = None
    try:
        for chunk in store.list_chunks(kb_ids, with_vector=False):
            if chunk.embedding_model or chunk.embedding_dim:
                found = (chunk.embedding_model, int(chunk.embedding_dim or 0))
                break
    except Exception as exc:  # noqa: BLE001 — 指纹查不到不该让检索失败
        print(f"[rag] 读取嵌入指纹失败（跳过校验）：{type(exc).__name__}: {exc}", flush=True)
        return None

    _FP_CACHE[key] = (now, *(found or ("", 0)))
    return found


def check_embedding_fingerprint(
    store: KBStore, kb_ids: list[str], *, model: str, dim: int,
) -> str | None:
    """索引向量是不是当前模型产的？一致返回 None，不一致返回人话说明。"""
    fingerprint = index_fingerprint(store, kb_ids)
    if not fingerprint:
        return None
    old_model, old_dim = fingerprint
    mismatch = (old_dim and dim and old_dim != dim) or (
        old_model and model and old_model != model
    )
    if not mismatch:
        return None
    return (
        f"知识库里的向量由 `{old_model or '未知模型'}`（{old_dim or '?'} 维）生成，"
        f"当前嵌入模型是 `{model}`（{dim} 维），两者不在同一向量空间——"
        "检索结果不可信（语义排序会近似随机）。请重建索引："
        "`python tools/rebuild_vector_index.py`"
    )


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
    - parent-child：先切父片（大、保上下文），父片内再切孩子片（小、检索准）。
      命中孩子片后把同父兄弟拼回去回答，兼顾召回精度与上下文完整
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

    if strategy == "parent-child":
        parents = _split(content, strategy="recursive", size=PARENT_SIZE, overlap=0)
        children: list[str] = []
        for parent in parents:
            children.extend(_split(parent, strategy="recursive",
                                   size=CHILD_SIZE, overlap=CHILD_OVERLAP))
        return [c for c in children if c.strip()] or [content]

    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    return splitter.split_text(content) or [content]


async def _embed_and_store(
    store: KBStore, doc: Document, kb_id: str, content: str, texts: list[str],
    *, strategy: str = DEFAULT_STRATEGY,
) -> int:
    """嵌入并写入切片，返回切片数。切片 id 用 `{doc_id}-{seq}`，删过文档也不会撞号。

    parent-child 策略额外标注 parent_seq：孩子片属于第几个父片，
    检索命中后按 (document_id, parent_seq) 把兄弟片拼回父片上下文。
    """
    parent_bounds: list[tuple[int, int]] = []
    if strategy == "parent-child":
        cursor_p = 0
        for parent in _split(content, strategy="recursive", size=PARENT_SIZE, overlap=0):
            start_p = content.find(parent, cursor_p)
            if start_p < 0:
                start_p = cursor_p
            parent_bounds.append((start_p, start_p + len(parent)))
            cursor_p = start_p + len(parent)

    def _parent_of(start: int) -> int | None:
        if not parent_bounds:
            return None
        for i, (s, e) in enumerate(parent_bounds):
            if s <= start < e:
                return i
        # 落在边界缝隙（如切片起点恰好在父片结尾）：归到最近的父片
        return len(parent_bounds) - 1 if start >= parent_bounds[-1][0] else 0

    vectors, kind = await embed_texts(texts)
    # 指纹：这条向量是谁产的。换模型后靠它判断"该重建索引了"
    model = get_settings().embedding_model if kind == "remote" else "mock"
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
                embedding_model=model, embedding_dim=len(vec),
                char_start=start, char_end=end, created_at=now_iso(),
                parent_seq=_parent_of(start) if parent_bounds else None,
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
        count = await _embed_and_store(store, doc, kb_id, content, texts, strategy=strategy)
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
    count = await _embed_and_store(store, doc, kb_id, content, texts, strategy=strategy)
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
    model = get_settings().embedding_model if kind == "remote" else "mock"
    store.update_chunk(  # 内部已落盘
        chunk_id, text=text, vector=vectors[0], embedding_kind=kind,
        embedding_model=model, embedding_dim=len(vectors[0]),
    )
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
    *,
    rerank: bool = True,
    parent_expand: bool = False,
    min_score: float | None = None,
    max_per_doc: int | None = None,
) -> list[RetrievedChunk]:
    """检索：mode 为空时用 Settings.retrieval_mode（默认 hybrid）。

    rerank：开启时用配置的重排提供者对融合结果打绝对相关分（失败自动回退原序）。
    parent_expand：命中父子切片的孩子片后，把同父兄弟片文本拼回去（对话注入用）。
    min_score：相关性门限（默认 `Settings.retrieval_min_score`）。低于门限的命中
        直接丢弃，一条都不剩时返回空列表——由上层明确告诉模型/用户「未命中」，
        而不是把 3 条噪音当证据。0 = 关闭门限。
    max_per_doc：同一文档最多贡献几个片段（默认 `Settings.retrieval_max_per_doc`）。
    """
    if not kb_ids:
        return []
    settings = get_settings()
    mode = mode or settings.retrieval_mode  # type: ignore[assignment]
    floor = settings.retrieval_min_score if min_score is None else float(min_score)
    cap = settings.retrieval_max_per_doc if max_per_doc is None else int(max_per_doc)
    width = max(top_k * CANDIDATE_FACTOR, top_k)
    index = get_vector_index()

    qvec: list[float] | None = None
    if mode in ("vector", "hybrid"):
        embedded, _ = await embed_texts([query])
        qvec = embedded[0]
        # 检索前先对一次指纹：换了嵌入模型却没重建索引时，余弦排序等于随机，
        # 与其把噪音当证据喂给模型，不如明确告诉用户去重建。
        problem = check_embedding_fingerprint(
            store, kb_ids, model=settings.embedding_model, dim=len(qvec)
        )
        if problem:
            raise EmbeddingMismatch(problem)

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
        # 词法/混合：BM25 必须在目标库全量文本上建索引。
        # 只有内存向量后端需要向量列（要用它现算余弦），Milvus 走 ANN 索引，
        # 把 4096 维浮点一起读出来纯属白读——几千条就是几十 MB。
        need_vec = settings.vector_backend == "memory"
        candidates = store.list_chunks(kb_ids, with_vector=need_vec)
        if not candidates:
            return []
        if qvec is not None:
            try:
                vec_hits = index.search(kb_ids, qvec, width, chunks=candidates)
            except VectorIndexError as exc:
                print(f"[rag] 向量索引不可用，降级内存打散：{exc}", flush=True)
                if not need_vec:
                    # 降级路径才需要向量，补一次带向量的查询
                    candidates = store.list_chunks(kb_ids, with_vector=True)
                vec_hits = MemoryIndex.score(candidates, qvec, width)

    by_id = {c.id: c for c in candidates}
    # 索引里可能残留已删切片，统一用回表结果兜底。
    # 这里只做「候选池」的粗剪（门限的一半），真正的判定在排序完成后按
    # 绝对相关分做，避免过早在 RRF 融合前把可能有用的候选砍光。
    soft_floor = floor * 0.6 if floor > 0 else 0.0
    vec_ranked = [(cid, s) for cid, s in vec_hits if s >= soft_floor and cid in by_id]
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
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        width = max(top_k * 4, top_k)          # 融合候选多留些，重排后才截断
        ordered = ordered[:width]
    elif mode == "vector":
        ordered = vec_ranked[: max(top_k * 4, top_k)]
    else:
        ordered = lex_ranked[: max(top_k * 4, top_k)]

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

    if rerank and hits:
        hits, _info = await rerank_hits(query, hits, top_k)
    else:
        hits = hits[:top_k]

    # 先过相关性门限，再按文档配额截断：顺序反了会把「同一篇文档占满名额」
    # 误判成「命中不足」。
    hits = apply_score_gate(query, hits, min_score=floor)
    hits = cap_per_document(hits, max_per_doc=cap)

    if parent_expand:
        hits = expand_parents(store, hits)
    return hits


def _term_coverage(query: str, text: str) -> float:
    """查询词在片段里的覆盖率：没有向量分时用它判断「是否真的词面命中」。

    纯词法命中的片段没有余弦分可比，但"查询里的词有多少个出现在这段文本里"
    是个绝对量——问"报销标准"却只匹配到"标准"一个词，覆盖率 0.5 以下就该丢。
    """
    from app.services.lexical import tokenize

    terms = set(tokenize(query))
    if not terms:
        return 0.0
    body = set(tokenize(text))
    return len(terms & body) / len(terms)


def apply_score_gate(
    query: str, hits: list[RetrievedChunk], *, min_score: float,
) -> list[RetrievedChunk]:
    """相关性门限：拿绝对分（重排分 > 余弦）判定，词法命中用词覆盖率兜底。

    这是「知识库未命中」这条分支能否成立的关键——阈值取 0.01 时任何问题都能
    拿到 3 条片段，模型于是把噪音当证据，还被系统提示要求「以知识库为准」。
    """
    if min_score <= 0:
        return hits
    kept: list[RetrievedChunk] = []
    for h in hits:
        absolute = h.rerank_score if h.rerank_score is not None else h.vector_score
        if absolute is not None:
            if absolute >= min_score:
                kept.append(h)
            continue
        # 纯词法命中：词覆盖率达到一半才算相关
        if _term_coverage(query, h.text) >= 0.5:
            kept.append(h)
    if not kept and hits:
        # 全部被砍时把最高分打出来：门限是按模型标定的，日志是标定的抓手
        best = max(
            (h.rerank_score if h.rerank_score is not None else (h.vector_score or 0.0))
            for h in hits
        )
        print(f"[rag] 全部命中低于相关性门限 {min_score:.2f}（最高 {best:.2f}），按未命中处理",
              flush=True)
    return kept


def cap_per_document(hits: list[RetrievedChunk], *, max_per_doc: int) -> list[RetrievedChunk]:
    """同一文档最多贡献 N 个片段，把名额留给跨文档证据。"""
    if max_per_doc <= 0:
        return hits
    seen: dict[str, int] = {}
    out: list[RetrievedChunk] = []
    for h in hits:
        used = seen.get(h.document_id, 0)
        if used >= max_per_doc:
            continue
        seen[h.document_id] = used + 1
        out.append(h)
    return out


def expand_parents(store: KBStore, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """父子切片回填：命中孩子片后，把同父兄弟片的文本按 seq 拼回父片。

    非父子切片（parent_seq 为 None）原样保留。排序分取同父孩子片中的最高分。
    """
    parent_groups: dict[tuple[str, int], list[RetrievedChunk]] = {}
    standalone: list[RetrievedChunk] = []
    for h in hits:
        chunks = store.get_chunks([h.chunk_id])
        parent_seq = chunks[0].parent_seq if chunks else None
        if parent_seq is None:
            standalone.append(h)
        else:
            parent_groups.setdefault((h.document_id, parent_seq), []).append(h)

    out: list[RetrievedChunk] = list(standalone)
    for (_doc_id, _pseq), group in parent_groups.items():
        siblings = [c for c in store.list_chunks(document_id=group[0].document_id)
                    if c.parent_seq == _pseq]
        siblings.sort(key=lambda c: c.seq)
        best = max(group, key=lambda h: h.score)
        out.append(RetrievedChunk(
            chunk_id=best.chunk_id,
            document_id=best.document_id,
            document_name=best.document_name,
            text="\n".join(c.text for c in siblings),
            score=best.score,
            vector_score=best.vector_score,
            lexical_score=best.lexical_score,
            rerank_score=best.rerank_score,
        ))
    return out


def build_context_block(hits: list[RetrievedChunk]) -> str:
    """检索结果 → 注入提示词的上下文块。"""
    parts = [f"[{i + 1}] 来源：{h.document_name}\n{h.text}" for i, h in enumerate(hits)]
    return "\n\n".join(parts)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
