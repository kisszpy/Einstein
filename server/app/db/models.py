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


# ---------------------------------------------------------------- 会话域


@dataclass
class StoredMessage:
    role: str
    content: str
    created_at: str


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    # 只在列表查询时填充（避免为了显示条数把全部消息读出来）；详情查询时为空
    message_count: int = 0
    messages: list[StoredMessage] = field(default_factory=list)
    workspace_id: str = DEFAULT_WS


@dataclass
class AgentRun:
    id: str
    task: str
    tool_ids: list[str]
    steps: list[dict]
    answer: str
    status: str  # ok | error
    created_at: str
    workspace_id: str = DEFAULT_WS
    project: str = ""
    artifacts: list[dict] = field(default_factory=list)
    # 同一任务的后续追问共享 session_id；turn 从 1 开始递增
    session_id: str = ""
    turn: int = 1
