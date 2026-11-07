"""向量索引协议。

为什么单独抽一层：检索是 RAG 的主路径，也是数据量涨上来后第一个撑不住的环节。
- memory：numpy 批量余弦，单机小库（< 10 万切片）够用，零运维
- milvus：HNSW + COSINE，按 kb_id 标量过滤，千万级

两种实现的返回语义必须一致：`[(chunk_id, cosine_similarity)]`，按分降序，
相似度在 [-1, 1]，越大越相近。上层只依赖这个语义，换后端不改检索代码。
"""

from __future__ import annotations

from typing import Protocol

from app.db.models import Chunk


class VectorIndexError(RuntimeError):
    """索引不可用（连不上 Milvus、维度冲突等）。由调用方决定降级还是报错。"""


class VectorIndex(Protocol):
    backend: str

    def search(
        self, kb_ids: list[str], vector: list[float], top_k: int,
        *, chunks: list[Chunk] | None = None,
    ) -> list[tuple[str, float]]:
        """返回 [(chunk_id, similarity)]，降序。

        `chunks` 是调用方已经取到的候选切片，内存实现直接用它算，
        避免同一个请求把切片读两遍；Milvus 实现忽略它。
        """
        ...

    def upsert(self, chunks: list[Chunk]) -> int:
        """写入或覆盖切片向量，返回写入条数。内存实现是空操作。"""
        ...

    def delete_document(self, document_id: str) -> None: ...

    def delete_kb(self, kb_id: str) -> None: ...

    def count(self, kb_id: str | None = None) -> int: ...

    def rebuild(self, chunks: list[Chunk] | None = None) -> int:
        """丢弃并重建索引；chunks 为空时由实现自行从存储加载。"""
        ...
