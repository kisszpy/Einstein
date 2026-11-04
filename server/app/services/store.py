"""知识库存储：开发期内存适配器 + JSON 文件持久化。

生产替换点：Milvus（向量）+ PostgreSQL（元数据），接口保持不变。
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "kb_store.json"


@dataclass
class Chunk:
    id: str
    kb_id: str
    document_id: str
    text: str
    vector: list[float]
    embedding_kind: str = "mock"


@dataclass
class Document:
    id: str
    kb_id: str
    name: str
    content: str
    chunk_count: int = 0


@dataclass
class KnowledgeBase:
    id: str
    name: str
    created_at: str = ""


@dataclass
class KBStore:
    kbs: dict[str, KnowledgeBase] = field(default_factory=dict)
    documents: dict[str, Document] = field(default_factory=dict)
    chunks: list[Chunk] = field(default_factory=list)

    def add_kb(self, name: str) -> KnowledgeBase:
        kb = KnowledgeBase(id=uuid.uuid4().hex[:12], name=name)
        self.kbs[kb.id] = kb
        return kb

    def add_document(self, kb_id: str, name: str, content: str, chunk_count: int) -> Document:
        doc = Document(id=uuid.uuid4().hex[:12], kb_id=kb_id, name=name,
                       content=content, chunk_count=chunk_count)
        self.documents[doc.id] = doc
        return doc

    def add_chunk(self, chunk: Chunk) -> None:
        self.chunks.append(chunk)

    def chunks_of_kb(self, kb_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.kb_id == kb_id]

    def list_kbs(self) -> list[KnowledgeBase]:
        return list(self.kbs.values())

    def documents_of_kb(self, kb_id: str) -> list[Document]:
        return [d for d in self.documents.values() if d.kb_id == kb_id]

    # ---- 持久化 ----
    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        payload = {
            "kbs": {k: asdict(v) for k, v in self.kbs.items()},
            "documents": {k: asdict(v) for k, v in self.documents.items()},
            "chunks": [asdict(c) for c in self.chunks],
        }
        STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "KBStore":
        if not STORE_PATH.exists():
            return cls()
        try:
            payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            store = cls()
            store.kbs = {k: KnowledgeBase(**v) for k, v in payload.get("kbs", {}).items()}
            store.documents = {
                k: Document(**v) for k, v in payload.get("documents", {}).items()
            }
            store.chunks = [Chunk(**c) for c in payload.get("chunks", [])]
            return store
        except Exception:  # noqa: BLE001 — 损坏则重建
            return cls()


_store: KBStore | None = None
_lock = threading.Lock()


def get_store() -> KBStore:
    global _store
    with _lock:
        if _store is None:
            _store = KBStore.load()
        return _store
