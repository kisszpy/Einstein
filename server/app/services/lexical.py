"""BM25 词法检索（jieba 中文分词）。

为什么需要它：向量检索擅长语义相近，但对**专有名词、术语缩写、公式编号**
这类精确匹配较弱；BM25 恰好相反。两者用 RRF 融合能互补。

实现是内存倒排；生产替换点：Milvus 2.5 全文检索 / Elasticsearch，接口保持不变。

分词结果是这里最贵的一步（千级切片约 1 秒），所以按 chunk_id 缓存 tf：
切片文本变了 hash 就对不上，自动重算；未变的直接复用。删除切片留下的
孤儿条目会在缓存涨到上限时整体清掉。
"""

import re
import threading
from collections import Counter

import jieba

# 极简停用词：只去掉高频虚词与疑问词，避免影响专有名词命中
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "等", "中", "对", "为",
    "把", "被", "一个", "什么", "怎么", "如何", "哪些", "哪个", "为什么",
    "了", "吗", "呢", "吧", "有", "没有", "可以", "请", "说明",
}

_PUNCT = re.compile(r"^[\W_]+$", re.UNICODE)
_initialized = False

# chunk_id -> (文本 hash, 词频, 长度)；只缓存分词结果，几万条量级内存可忽略
_TF_CACHE: dict[str, tuple[int, Counter[str], int]] = {}
_TF_CACHE_MAX = 200_000
_cache_lock = threading.Lock()


def _ensure_init() -> None:
    """jieba 首次加载词典约 0.5s，延迟到第一次真实检索。"""
    global _initialized
    if not _initialized:
        jieba.initialize()
        _initialized = True


def tokenize(text: str) -> list[str]:
    _ensure_init()
    tokens: list[str] = []
    for raw in jieba.cut(text):
        t = raw.strip().lower()
        if not t or t in _STOPWORDS or _PUNCT.match(t):
            continue
        tokens.append(t)
    return tokens


def _term_freq(doc_id: str, text: str, *, cache: bool) -> tuple[Counter[str], int]:
    """取一篇的 (词频, 长度)；命中缓存时跳过分词。"""
    if not cache:
        tokens = tokenize(text)
        return Counter(tokens), len(tokens)
    stamp = hash(text)
    with _cache_lock:
        hit = _TF_CACHE.get(doc_id)
    if hit is not None and hit[0] == stamp:
        return hit[1], hit[2]
    tokens = tokenize(text)
    tf = Counter(tokens)
    with _cache_lock:
        if len(_TF_CACHE) >= _TF_CACHE_MAX:
            _TF_CACHE.clear()
        _TF_CACHE[doc_id] = (stamp, tf, len(tokens))
    return tf, len(tokens)


class BM25:
    """Okapi BM25，k1/b 取常用默认值。"""

    def __init__(
        self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75,
        *, cache: bool = True,
    ):
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = []
        self.term_freqs: list[Counter[str]] = []
        self.doc_lens: list[int] = []
        df: Counter[str] = Counter()

        for doc_id, text in docs:
            tf, length = _term_freq(doc_id, text, cache=cache)
            self.doc_ids.append(doc_id)
            self.term_freqs.append(tf)
            self.doc_lens.append(length)
            for term in tf:
                df[term] += 1

        self.n_docs = len(self.doc_ids)
        self.avg_len = (sum(self.doc_lens) / self.n_docs) if self.n_docs else 0.0
        # idf 采用 Lucene 口径（加 1 避免负值）
        self.idf = {
            term: 1.0 + (self.n_docs - freq + 0.5) / (freq + 0.5)
            for term, freq in df.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        q_terms = tokenize(query)
        if not q_terms or not self.n_docs:
            return []
        scores: list[tuple[str, float]] = []
        for idx, doc_id in enumerate(self.doc_ids):
            tf = self.term_freqs[idx]
            dl = self.doc_lens[idx] or 1
            score = 0.0
            for term in q_terms:
                f = tf.get(term)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1))
                score += idf * f * (self.k1 + 1) / denom
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda kv: kv[1], reverse=True)
        return scores[:top_k]
