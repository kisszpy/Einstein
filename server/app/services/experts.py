"""专家存储：专家 = 配置态（人设 + 挂技能 + 挂知识库 + 模型参数）。

专家是组装产物：对话时合成系统提示（人设 + 技能提示词），
检索与工具按挂载的资源自动启用。生产替换点：PostgreSQL。
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "experts_store.json"


@dataclass
class Expert:
    id: str
    name: str
    description: str = ""
    emoji: str = "🤖"
    persona: str = ""  # 人设（核心系统提示）
    skill_ids: list[str] = field(default_factory=list)
    kb_ids: list[str] = field(default_factory=list)
    tool_ids: list[str] = field(default_factory=list)
    temperature: float = 0.7
    enabled: bool = True
    workspace_id: str = "default"


class ExpertStore:
    experts: dict[str, Expert]

    def __init__(self) -> None:
        self.experts = {}

    def list_experts(self, workspace_id: str | None = None) -> list[Expert]:
        rows = list(self.experts.values())
        if workspace_id:
            rows = [e for e in rows if e.workspace_id == workspace_id]
        return rows

    def get(self, expert_id: str) -> Expert | None:
        return self.experts.get(expert_id)

    def add(self, **kwargs: object) -> Expert:
        expert = Expert(
            id=uuid.uuid4().hex[:12],
            name=kwargs.get("name", "未命名专家"),  # type: ignore[arg-type]
            description=kwargs.get("description", ""),  # type: ignore[arg-type]
            emoji=kwargs.get("emoji", "🤖"),  # type: ignore[arg-type]
            persona=kwargs.get("persona", ""),  # type: ignore[arg-type]
            skill_ids=kwargs.get("skill_ids", []),  # type: ignore[arg-type]
            kb_ids=kwargs.get("kb_ids", []),  # type: ignore[arg-type]
            tool_ids=kwargs.get("tool_ids", []),  # type: ignore[arg-type]
            temperature=kwargs.get("temperature", 0.7),  # type: ignore[arg-type]
            workspace_id=kwargs.get("workspace_id", "default"),  # type: ignore[arg-type]
        )
        self.experts[expert.id] = expert
        return expert

    def update(self, expert_id: str, **fields: object) -> Expert | None:
        expert = self.experts.get(expert_id)
        if expert is None:
            return None
        for key, value in fields.items():
            if value is not None and hasattr(expert, key):
                setattr(expert, key, value)
        return expert

    def delete(self, expert_id: str) -> bool:
        if expert_id in self.experts:
            del self.experts[expert_id]
            return True
        return False

    # ---- 持久化 ----
    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        payload = {k: asdict(v) for k, v in self.experts.items()}
        STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "ExpertStore":
        store = cls()
        if STORE_PATH.exists():
            try:
                payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
                store.experts = {k: Expert(**v) for k, v in payload.items()}
            except Exception:  # noqa: BLE001 — 损坏则重建
                pass
        return store


_expert_store: ExpertStore | None = None
_lock = threading.Lock()


def get_expert_store() -> ExpertStore:
    global _expert_store
    with _lock:
        if _expert_store is None:
            _expert_store = ExpertStore.load()
        return _expert_store
