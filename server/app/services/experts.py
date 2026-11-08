"""专家存储：专家 = 配置态（人设 + 挂技能 + 挂知识库 + 模型参数）。

专家是组装产物：对话时合成系统提示（人设 + 技能提示词），
检索与工具按挂载的资源自动启用。生产替换点：PostgreSQL。
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "experts_store.json"
_STATE = blob("experts", STORE_PATH)

# 发布流状态机：draft（编辑中）→ testing（内部测试）→ live（对话页可见）
STATUSES = ("draft", "testing", "live")
TRANSITIONS = {
    "draft": {"testing"},
    "testing": {"live", "draft"},
    "live": {"testing"},  # 下架回测试
}


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
    # ---- 结构化人设与发布流 ----
    greeting: str = ""        # 开场白（选中专家后展示在空会话里）
    suggestions: list[str] = field(default_factory=list)  # 推荐问题（点击即发送）
    status: str = "draft"     # draft | testing | live
    published_at: str = ""    # 首次上线时间


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
            greeting=kwargs.get("greeting", ""),  # type: ignore[arg-type]
            suggestions=kwargs.get("suggestions", []),  # type: ignore[arg-type]
            status=kwargs.get("status", "draft"),  # type: ignore[arg-type]
        )
        self.experts[expert.id] = expert
        return expert

    def publish(self, expert_id: str, status: str) -> Expert | None:
        """按状态机推进发布流，返回 None 表示非法转移。"""
        expert = self.experts.get(expert_id)
        if expert is None or status not in STATUSES:
            return None
        if status not in TRANSITIONS.get(expert.status, set()):
            return None
        expert.status = status
        if status == "live":
            expert.published_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
        payload = {k: asdict(v) for k, v in self.experts.items()}
        _STATE.write(payload)

    @classmethod
    def load(cls) -> "ExpertStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.experts = {k: Expert(**v) for k, v in payload.items()}
            # 存量数据没有 status 字段：视为已上线，避免升级后对话页专家消失
            for key, v in payload.items():
                if key in store.experts and "status" not in v:
                    store.experts[key].status = "live"
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
