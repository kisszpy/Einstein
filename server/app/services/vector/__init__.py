"""向量索引工厂。

`vector_backend` 决定实现：
- memory（默认）：numpy 现算，无状态、无运维、不丢数据
- milvus：HNSW 持久索引，需要 Milvus 服务

`safe_*` 系列是给写入路径用的：索引只是加速结构，数据库才是真相源。
索引写失败绝不能让入库失败，因此这里吞异常并打日志，后续可 rebuild 修复。
"""

from __future__ import annotations

import threading

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Chunk
from app.services.vector.base import VectorIndex, VectorIndexError
from app.services.vector.memory import MemoryIndex
from app.services.vector.milvus import MilvusIndex

logger = get_logger(__name__)

__all__ = [
    "MemoryIndex",
    "MilvusIndex",
    "VectorIndex",
    "VectorIndexError",
    "get_vector_index",
    "reset_vector_index",
    "safe_delete_document",
    "safe_delete_kb",
    "safe_upsert",
]

_index: VectorIndex | None = None
_lock = threading.Lock()


def _build() -> VectorIndex:
    s = get_settings()
    if s.vector_backend == "milvus":
        # 延迟导入：store 的删除路径会回调本模块，顶层导入会成环
        from app.services.store import get_store

        return MilvusIndex(
            s.milvus_uri, s.milvus_collection,
            bootstrap=lambda: get_store().list_chunks(),
        )
    from app.services.store import get_store

    return MemoryIndex(loader=lambda kb_ids: get_store().list_chunks(kb_ids))


def get_vector_index() -> VectorIndex:
    global _index
    with _lock:
        if _index is None:
            _index = _build()
        return _index


def reset_vector_index() -> None:
    global _index
    with _lock:
        _index = None


def safe_upsert(chunks: list[Chunk]) -> None:
    if not chunks:
        return
    try:
        get_vector_index().upsert(chunks)
    except Exception as exc:  # noqa: BLE001 — 索引失败不影响入库
        logger.warning("索引写入失败（数据已入库，可 rebuild 修复）：%s", exc)


def safe_delete_document(document_id: str) -> None:
    try:
        get_vector_index().delete_document(document_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除文档向量失败：%s", exc)


def safe_delete_kb(kb_id: str) -> None:
    try:
        get_vector_index().delete_kb(kb_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除知识库向量失败：%s", exc)
