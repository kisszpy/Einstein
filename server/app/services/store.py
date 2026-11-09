"""知识库门面。

对外保持 v0.1 的方法签名（`add_kb` / `owns` / `add_document` / `add_chunk` / `save` ...），
内部按 `settings.store_backend` 委派给 JSON 或 SQL 仓储：
- json   → 全量内存 + 单文件（v0.1 行为，无数据库时的降级路径）
- sqlite → 单机事务（WAL），默认推荐给本地开发
- postgres → 生产

为什么保留 `kbs` / `documents` / `chunks` 属性：v0.1 有 20 余处直接读取它们做统计或遍历。
属性仍可用，但热路径（检索）必须走 `list_chunks(kb_ids)` 只取目标知识库的切片，
否则规模化改造等于没做。
"""

from __future__ import annotations

import threading
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.models import DEFAULT_WS, Chunk, Document, KnowledgeBase
from app.db.schema import migrate
from app.services.repositories.kb import JsonKbRepository, SqlKbRepository

__all__ = ["Chunk", "Document", "KnowledgeBase", "KBStore", "DEFAULT_WS", "get_store"]

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "kb_store.json"


class KBStore:
    def __init__(self, repo) -> None:  # noqa: ANN001 — Json/Sql 仓储，鸭子类型
        self.repo = repo

    # ---------- 兼容属性（只读语义；写请用方法） ----------
    @property
    def kbs(self) -> dict[str, KnowledgeBase]:
        return {k.id: k for k in self.repo.list_kbs()}

    @property
    def documents(self) -> dict[str, Document]:
        rows = self.repo.list_documents_all() if hasattr(self.repo, "list_documents_all") else []
        return {d.id: d for d in rows}

    @property
    def chunks(self) -> list[Chunk]:
        return self.repo.list_chunks()

    # ---------- 知识库 ----------
    def add_kb(self, name: str, workspace_id: str = DEFAULT_WS, owner_id: str = "") -> KnowledgeBase:
        kb = self.repo.create_kb(name, workspace_id, owner_id=owner_id)
        self.repo.save()
        return kb

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        return self.repo.get_kb(kb_id)

    def owns(self, kb_id: str, workspace_id: str, owner_id: str | None = None) -> bool:
        return self.repo.owns(kb_id, workspace_id, owner_id)

    def owned_kb_ids(self, workspace_id: str, kb_ids: list[str], owner_id: str | None = None) -> list[str]:
        """把外部传入的 kb_ids 收敛到「当前工作区 + 当前用户」可见的集合。

        两个维度缺一不可：workspace 防跨空间越权，owner 防同空间里看别人的库。
        """
        return [i for i in kb_ids if self.repo.owns(i, workspace_id, owner_id)]

    def list_kbs(self, workspace_id: str | None = None, owner_id: str | None = None) -> list[KnowledgeBase]:
        return self.repo.list_kbs(workspace_id, owner_id)

    def delete_kb(self, kb_id: str) -> int:
        """删除知识库及其文档/切片，返回被删文档数。"""
        removed = len(self.repo.list_documents(kb_id)) if hasattr(self.repo, "list_documents") else 0
        self.repo.delete_kb(kb_id)
        self.repo.save()
        # 持久化向量索引必须跟着删，否则删库后仍能检索到已删内容
        from app.services.vector import safe_delete_kb

        safe_delete_kb(kb_id)
        return removed

    # ---------- 文档 ----------
    def add_document(self, kb_id: str, name: str, content: str, chunk_count: int, **kwargs) -> Document:
        doc = self.repo.add_document(kb_id, name, content, chunk_count, **kwargs)
        self.repo.save()
        return doc

    def update_document(self, doc_id: str, **fields) -> Document | None:
        doc = self.repo.update_document(doc_id, **fields)
        self.repo.save()
        return doc

    def get_document(self, doc_id: str) -> Document | None:
        return self.repo.get_document(doc_id)

    def documents_of_kb(self, kb_id: str) -> list[Document]:
        return self.repo.list_documents(kb_id)

    def find_document_by_hash(self, kb_id: str, content_hash: str) -> Document | None:
        return self.repo.find_document_by_hash(kb_id, content_hash)

    def delete_document(self, doc_id: str) -> int:
        """删除单篇文档并级联清理切片，返回被删切片数。"""
        removed = self.repo.delete_document(doc_id)
        self.repo.save()
        from app.services.vector import safe_delete_document

        safe_delete_document(doc_id)
        return removed

    def doc_names(self) -> dict[str, str]:
        """文档 id → 名称，供检索结果回填来源名（避免加载正文）。"""
        if hasattr(self.repo, "doc_names"):
            return self.repo.doc_names()
        return {d.id: d.name for d in self.documents.values()}

    # ---------- 切片 ----------
    def add_chunk(self, chunk: Chunk) -> None:
        self.repo.add_chunks([chunk])

    def add_chunks(self, chunks: list[Chunk]) -> None:
        self.repo.add_chunks(chunks)
        self.repo.save()
        # 持久化向量索引双写：写库成功后同步索引，检索才能立刻命中
        from app.services.vector import safe_upsert

        safe_upsert(chunks)

    def list_chunks(
        self, kb_ids: list[str] | None = None, document_id: str | None = None,
        *, with_vector: bool = True,
    ) -> list[Chunk]:
        """按知识库直接取切片：检索热路径用它，避免把全库向量读进内存。

        with_vector=False 用于词法检索——BM25 只要文本，带向量会白读几十 MB。
        """
        return self.repo.list_chunks(kb_ids, document_id, with_vector=with_vector)

    def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]:
        """按 id 回表：向量索引命中后只取这几条（纯向量检索路径）。"""
        return self.repo.get_chunks(chunk_ids)

    def update_chunk(
        self, chunk_id: str, *, text: str, vector: list[float], embedding_kind: str = "mock",
    ) -> bool:
        """改切片文本与向量。与其它写方法一致，落盘由 store 负责：
        SQL 后端的 save() 是空操作，JSON 后端则在这里原子重写整个文件。"""
        ok = self.repo.update_chunk(
            chunk_id, text=text, vector=vector, embedding_kind=embedding_kind
        )
        if ok:
            self.repo.save()
        return ok

    def delete_chunks_of_document(self, doc_id: str) -> int:
        removed = self.repo.delete_chunks_by_document(doc_id)
        self.repo.save()
        return removed

    def chunks_of_kb(self, kb_id: str) -> list[Chunk]:
        return self.repo.list_chunks([kb_id])

    def count_chunks(self, kb_id: str) -> int:
        return self.repo.count_chunks(kb_id)

    # ---------- 持久化 ----------
    def save(self) -> None:
        self.repo.save()


_store: KBStore | None = None
_lock = threading.Lock()


def _build_repo():  # noqa: ANN202
    backend = get_settings().store_backend
    if backend in ("sqlite", "postgres"):
        db = get_db()
        migrate(db)
        return SqlKbRepository(db)
    return JsonKbRepository(STORE_PATH)


def get_store() -> KBStore:
    global _store
    with _lock:
        if _store is None:
            _store = KBStore(_build_repo())
        return _store


def reset_store() -> None:
    """测试用：换后端或换库后重新构建。"""
    global _store
    with _lock:
        _store = None
