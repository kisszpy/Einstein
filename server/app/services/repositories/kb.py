"""知识库仓储：同一套语义的 JSON / SQL 两种实现。

上层只依赖本文件定义的方法（KBStore 门面转发），因此切换存储后端不需要改路由与检索代码。
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Protocol

import numpy as np

from app.db.models import DEFAULT_WS, Chunk, Document, KnowledgeBase

# ---------------------------------------------------------------- 向量编解码


def vec_to_bytes(vec: list[float]) -> bytes | None:
    if not vec:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


def bytes_to_vec(raw: bytes | memoryview | None) -> list[float]:
    if not raw:
        return []
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    return np.frombuffer(raw, dtype=np.float32).tolist()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- 协议
class KbRepository(Protocol):
    def list_kbs(self, workspace_id: str | None = None) -> list[KnowledgeBase]: ...
    def get_kb(self, kb_id: str) -> KnowledgeBase | None: ...
    def create_kb(self, name: str, workspace_id: str = DEFAULT_WS, description: str = "") -> KnowledgeBase: ...
    def delete_kb(self, kb_id: str) -> bool: ...
    def owns(self, kb_id: str, workspace_id: str) -> bool: ...
    def add_document(
        self, kb_id: str, name: str, content: str, chunk_count: int,
        *, source: str = "text", status: str = "ready", content_hash: str = "", meta: dict | None = None,
    ) -> Document: ...
    def update_document(self, doc_id: str, **fields) -> Document | None: ...
    def list_documents(self, kb_id: str) -> list[Document]: ...
    def get_document(self, doc_id: str) -> Document | None: ...
    def find_document_by_hash(self, kb_id: str, content_hash: str) -> Document | None: ...
    def delete_document(self, doc_id: str) -> int: ...
    def add_chunks(self, chunks: list[Chunk]) -> None: ...
    def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]: ...
    def list_chunks(self, kb_ids: list[str] | None = None, document_id: str | None = None) -> list[Chunk]: ...
    def delete_chunks_by_document(self, doc_id: str) -> int: ...
    def count_chunks(self, kb_id: str) -> int: ...
    def save(self) -> None: ...


# ---------------------------------------------------------------- JSON 实现
class JsonKbRepository:
    """v0.1 行为：全量内存 + 单文件 JSON。保留它有两个作用：
    1) 迁移期做对照（同一份数据在两种后端上跑同一套断言）
    2) 无数据库环境时的降级路径
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.kbs: dict[str, KnowledgeBase] = {}
        self.documents: dict[str, Document] = {}
        self.chunks: list[Chunk] = []
        self._lock = threading.RLock()
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 损坏则重建，与 v0.1 一致
            return
        from dataclasses import fields

        kb_fields = {f.name for f in fields(KnowledgeBase)}
        doc_fields = {f.name for f in fields(Document)}
        chunk_fields = {f.name for f in fields(Chunk)}
        self.kbs = {
            k: KnowledgeBase(**{kk: vv for kk, vv in v.items() if kk in kb_fields})
            for k, v in (payload.get("kbs") or {}).items()
        }
        self.documents = {
            k: Document(**{kk: vv for kk, vv in v.items() if kk in doc_fields})
            for k, v in (payload.get("documents") or {}).items()
        }
        self.chunks = [
            Chunk(**{kk: vv for kk, vv in c.items() if kk in chunk_fields})
            for c in (payload.get("chunks") or [])
        ]

    def save(self) -> None:
        from dataclasses import asdict

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "kbs": {k: asdict(v) for k, v in self.kbs.items()},
                "documents": {k: asdict(v) for k, v in self.documents.items()},
                "chunks": [asdict(c) for c in self.chunks],
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)  # 原子替换，避免写一半崩溃留下残缺文件

    # ---- KB ----
    def list_kbs(self, workspace_id: str | None = None) -> list[KnowledgeBase]:
        rows = list(self.kbs.values())
        if workspace_id:
            rows = [k for k in rows if k.workspace_id == workspace_id]
        return sorted(rows, key=lambda k: k.created_at)

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        return self.kbs.get(kb_id)

    def create_kb(self, name: str, workspace_id: str = DEFAULT_WS, description: str = "") -> KnowledgeBase:
        with self._lock:
            kb = KnowledgeBase(
                id=_new_id(), name=name, workspace_id=workspace_id,
                description=description, created_at=_now(), updated_at=_now(),
            )
            self.kbs[kb.id] = kb
            return kb

    def delete_kb(self, kb_id: str) -> bool:
        with self._lock:
            if self.kbs.pop(kb_id, None) is None:
                return False
            doc_ids = [d.id for d in self.documents.values() if d.kb_id == kb_id]
            for did in doc_ids:
                self.documents.pop(did, None)
            self.chunks = [c for c in self.chunks if c.kb_id != kb_id]
            return True

    def owns(self, kb_id: str, workspace_id: str) -> bool:
        kb = self.kbs.get(kb_id)
        return kb is not None and kb.workspace_id == workspace_id

    # ---- 文档 ----
    def add_document(
        self, kb_id: str, name: str, content: str, chunk_count: int,
        *, source: str = "text", status: str = "ready", content_hash: str = "", meta: dict | None = None,
    ) -> Document:
        with self._lock:
            doc = Document(
                id=_new_id(), kb_id=kb_id, name=name, content=content,
                chunk_count=chunk_count, content_hash=content_hash, source=source,
                status=status, meta=meta or {}, created_at=_now(), updated_at=_now(),
            )
            self.documents[doc.id] = doc
            return doc

    def update_document(self, doc_id: str, **fields) -> Document | None:
        with self._lock:
            doc = self.documents.get(doc_id)
            if doc is None:
                return None
            for key, value in fields.items():
                if hasattr(doc, key) and value is not None:
                    setattr(doc, key, value)
            doc.updated_at = _now()
            return doc

    def list_documents(self, kb_id: str) -> list[Document]:
        rows = [d for d in self.documents.values() if d.kb_id == kb_id]
        return sorted(rows, key=lambda d: d.created_at)

    def list_documents_all(self) -> list[Document]:
        return sorted(self.documents.values(), key=lambda d: d.created_at)

    def doc_names(self) -> dict[str, str]:
        return {d.id: d.name for d in self.documents.values()}

    def get_document(self, doc_id: str) -> Document | None:
        return self.documents.get(doc_id)

    def find_document_by_hash(self, kb_id: str, content_hash: str) -> Document | None:
        if not content_hash:
            return None
        for doc in self.documents.values():
            if doc.kb_id == kb_id and doc.content_hash == content_hash:
                return doc
        return None

    def delete_document(self, doc_id: str) -> int:
        with self._lock:
            self.documents.pop(doc_id, None)
            before = len(self.chunks)
            self.chunks = [c for c in self.chunks if c.document_id != doc_id]
            return before - len(self.chunks)

    # ---- 切片 ----
    def add_chunks(self, chunks: list[Chunk]) -> None:
        with self._lock:
            self.chunks.extend(chunks)

    def list_chunks(self, kb_ids: list[str] | None = None, document_id: str | None = None) -> list[Chunk]:
        rows = self.chunks
        if kb_ids is not None:
            allowed = set(kb_ids)
            rows = [c for c in rows if c.kb_id in allowed]
        if document_id:
            rows = [c for c in rows if c.document_id == document_id]
        return list(rows)

    def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        wanted = set(chunk_ids)
        return [c for c in self.chunks if c.id in wanted]

    def delete_chunks_by_document(self, doc_id: str) -> int:
        with self._lock:
            before = len(self.chunks)
            self.chunks = [c for c in self.chunks if c.document_id != doc_id]
            return before - len(self.chunks)

    def count_chunks(self, kb_id: str) -> int:
        return sum(1 for c in self.chunks if c.kb_id == kb_id)


# ---------------------------------------------------------------- SQL 实现
KB_COLUMNS = "id, workspace_id, name, description, visibility, created_at, updated_at"
DOC_COLUMNS = (
    "id, kb_id, name, content, content_hash, version, chunk_count, status, source, meta, created_at, updated_at"
)


class SqlKbRepository:
    def __init__(self, db) -> None:  # noqa: ANN001 — Database，避免循环导入
        self.db = db

    # ---- 行映射 ----
    @staticmethod
    def _kb(row: dict) -> KnowledgeBase:
        return KnowledgeBase(
            id=row["id"], name=row["name"], created_at=row["created_at"],
            workspace_id=row["workspace_id"], description=row.get("description") or "",
            visibility=row.get("visibility") or "workspace", updated_at=row.get("updated_at") or "",
        )

    @staticmethod
    def _doc(row: dict) -> Document:
        try:
            meta = json.loads(row.get("meta") or "{}")
        except Exception:  # noqa: BLE001
            meta = {}
        return Document(
            id=row["id"], kb_id=row["kb_id"], name=row["name"], content=row["content"],
            chunk_count=int(row["chunk_count"] or 0), content_hash=row["content_hash"] or "",
            version=int(row["version"] or 1), status=row["status"] or "ready",
            source=row["source"] or "text", meta=meta,
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _chunk(row: dict) -> Chunk:
        return Chunk(
            id=row["id"], kb_id=row["kb_id"], document_id=row["document_id"], text=row["text"],
            vector=bytes_to_vec(row.get("vector")), embedding_kind=row["vector_kind"] or "mock",
            seq=int(row["seq"] or 0), char_start=int(row["char_start"] or 0),
            char_end=int(row["char_end"] or 0), created_at=row["created_at"],
        )

    # ---- KB ----
    def list_kbs(self, workspace_id: str | None = None) -> list[KnowledgeBase]:
        if workspace_id:
            rows = self.db.query(
                f"SELECT {KB_COLUMNS} FROM knowledge_bases WHERE workspace_id = ? ORDER BY created_at",
                (workspace_id,),
            )
        else:
            rows = self.db.query(f"SELECT {KB_COLUMNS} FROM knowledge_bases ORDER BY created_at")
        return [self._kb(r) for r in rows]

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        row = self.db.query_one(f"SELECT {KB_COLUMNS} FROM knowledge_bases WHERE id = ?", (kb_id,))
        return self._kb(row) if row else None

    def create_kb(self, name: str, workspace_id: str = DEFAULT_WS, description: str = "") -> KnowledgeBase:
        now = _now()
        kb_id = _new_id()
        self.db.execute(
            "INSERT INTO knowledge_bases (id, workspace_id, name, description, visibility, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kb_id, workspace_id, name, description, "workspace", now, now),
        )
        return KnowledgeBase(
            id=kb_id, name=name, created_at=now, workspace_id=workspace_id,
            description=description, visibility="workspace", updated_at=now,
        )

    def delete_kb(self, kb_id: str) -> bool:
        with self.db.transaction():
            self.db.execute("DELETE FROM chunks WHERE kb_id = ?", (kb_id,))
            self.db.execute("DELETE FROM documents WHERE kb_id = ?", (kb_id,))
            affected = self.db.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
        return affected > 0

    def owns(self, kb_id: str, workspace_id: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 AS ok FROM knowledge_bases WHERE id = ? AND workspace_id = ?", (kb_id, workspace_id),
        )
        return row is not None

    # ---- 文档 ----
    def add_document(
        self, kb_id: str, name: str, content: str, chunk_count: int,
        *, source: str = "text", status: str = "ready", content_hash: str = "", meta: dict | None = None,
    ) -> Document:
        now = _now()
        doc_id = _new_id()
        self.db.execute(
            "INSERT INTO documents (id, kb_id, name, content, content_hash, version, chunk_count,"
            " status, source, meta, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, kb_id, name, content, content_hash, 1, chunk_count,
             status, source, json.dumps(meta or {}, ensure_ascii=False), now, now),
        )
        return Document(
            id=doc_id, kb_id=kb_id, name=name, content=content, chunk_count=chunk_count,
            content_hash=content_hash, version=1, status=status, source=source,
            meta=meta or {}, created_at=now, updated_at=now,
        )

    def update_document(self, doc_id: str, **fields) -> Document | None:
        allowed = {"name", "content", "content_hash", "version", "chunk_count", "status", "source", "meta"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed or value is None:
                continue
            sets.append(f"{key} = ?")
            params.append(json.dumps(value, ensure_ascii=False) if key == "meta" else value)
        if not sets:
            return self.get_document(doc_id)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(doc_id)
        self.db.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_document(doc_id)

    def list_documents(self, kb_id: str) -> list[Document]:
        rows = self.db.query(
            f"SELECT {DOC_COLUMNS} FROM documents WHERE kb_id = ? ORDER BY created_at", (kb_id,),
        )
        return [self._doc(r) for r in rows]

    def list_documents_all(self) -> list[Document]:
        rows = self.db.query(f"SELECT {DOC_COLUMNS} FROM documents ORDER BY created_at")
        return [self._doc(r) for r in rows]

    def doc_names(self) -> dict[str, str]:
        rows = self.db.query("SELECT id, name FROM documents")
        return {r["id"]: r["name"] for r in rows}

    def get_document(self, doc_id: str) -> Document | None:
        row = self.db.query_one(f"SELECT {DOC_COLUMNS} FROM documents WHERE id = ?", (doc_id,))
        return self._doc(row) if row else None

    def find_document_by_hash(self, kb_id: str, content_hash: str) -> Document | None:
        if not content_hash:
            return None
        row = self.db.query_one(
            f"SELECT {DOC_COLUMNS} FROM documents WHERE kb_id = ? AND content_hash = ? LIMIT 1",
            (kb_id, content_hash),
        )
        return self._doc(row) if row else None

    def delete_document(self, doc_id: str) -> int:
        with self.db.transaction():
            removed = self.db.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            self.db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return max(removed, 0)

    # ---- 切片 ----
    def add_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        now = _now()
        rows = [
            (
                c.id, c.kb_id, c.document_id, c.seq, c.text, c.char_start, c.char_end,
                vec_to_bytes(c.vector), c.embedding_kind, c.created_at or now,
            )
            for c in chunks
        ]
        self.db.executemany(
            "INSERT INTO chunks (id, kb_id, document_id, seq, text, char_start, char_end,"
            " vector, vector_kind, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def list_chunks(self, kb_ids: list[str] | None = None, document_id: str | None = None) -> list[Chunk]:
        where, params = [], []
        if kb_ids is not None:
            if not kb_ids:
                return []
            where.append(f"kb_id IN ({', '.join('?' for _ in kb_ids)})")
            params.extend(kb_ids)
        if document_id:
            where.append("document_id = ?")
            params.append(document_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.query(
            "SELECT id, kb_id, document_id, seq, text, char_start, char_end, vector,"
            f" vector_kind, created_at FROM chunks{clause} ORDER BY kb_id, document_id, seq",
            params,
        )
        return [self._chunk(r) for r in rows]

    def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]:
        """按 id 回表：向量索引命中后只取这几条，避免把整库向量读进内存。"""
        if not chunk_ids:
            return []
        marks = ", ".join("?" for _ in chunk_ids)
        rows = self.db.query(
            "SELECT id, kb_id, document_id, seq, text, char_start, char_end, vector,"
            f" vector_kind, created_at FROM chunks WHERE id IN ({marks})",
            list(chunk_ids),
        )
        return [self._chunk(r) for r in rows]

    def delete_chunks_by_document(self, doc_id: str) -> int:
        return max(self.db.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,)), 0)

    def count_chunks(self, kb_id: str) -> int:
        return int(self.db.scalar("SELECT count(*) AS n FROM chunks WHERE kb_id = ?", (kb_id,)) or 0)

    def save(self) -> None:
        """SQL 后端每次写就落库，save 保留为空操作以兼容旧调用点。"""
