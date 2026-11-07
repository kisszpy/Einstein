"""领域模型（跨存储后端共享）。

放在 db 层是为了让 repository 与 service 都能引用而不产生循环导入。
`app.services.store` 会 re-export 这几个类，历史代码的 `from app.services.store import Chunk` 继续可用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WS = "default"


@dataclass
class KnowledgeBase:
    id: str
    name: str
    created_at: str = ""
    workspace_id: str = DEFAULT_WS
    description: str = ""
    visibility: str = "workspace"   # private | workspace | shared
    updated_at: str = ""


@dataclass
class Document:
    id: str
    kb_id: str
    name: str
    content: str
    chunk_count: int = 0
    content_hash: str = ""
    version: int = 1
    status: str = "ready"           # pending | parsing | embedding | ready | failed
    source: str = "text"            # text | upload | url | xlsx | html ...
    meta: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Chunk:
    id: str
    kb_id: str
    document_id: str
    text: str
    vector: list[float]
    embedding_kind: str = "mock"
    seq: int = 0
    char_start: int = 0
    char_end: int = 0
    created_at: str = ""
