"""技能存储：技能是可复用的能力中间层（提示词模板 + 工具声明）。

专家挂载技能获得能力，智能体/应用引用专家。生产替换点：PostgreSQL。
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "skills_store.json"

# ---------- 内置技能种子（builtin 不可删，可编辑） ----------

BUILTIN_SKILLS = [
    {
        "id": "skill-general",
        "name": "通用助手",
        "description": "不附加任何风格约束，按模型默认方式回答",
        "system_prompt": "",
        "tools": [],
        "builtin": True,
    },
    {
        "id": "skill-writer",
        "name": "写作助手",
        "description": "结构化表达、结论先行、少废话",
        "system_prompt": (
            "你是资深写作助手。要求：\n"
            "1. 结论先行，先给答案再给理由；\n"
            "2. 用短段落和列表组织内容，层级清晰；\n"
            "3. 不用空洞的客套话和形容词堆砌；\n"
            "4. 涉及数据时给出来源或说明是估算。"
        ),
        "tools": [],
        "builtin": True,
    },
    {
        "id": "skill-coder",
        "name": "代码助手",
        "description": "代码审查、解释与重构建议",
        "system_prompt": (
            "你是资深工程师。要求：\n"
            "1. 先指出问题再给修改代码，修改用完整代码块展示；\n"
            "2. 说明修改理由和潜在影响面；\n"
            "3. 关注边界条件、错误处理和性能；\n"
            "4. 不确定的 API 行为要明确说不确定，不要编造。"
        ),
        "tools": [],
        "builtin": True,
    },
    {
        "id": "skill-analyst",
        "name": "数据分析",
        "description": "结合计算器与文本统计做量化分析",
        "system_prompt": (
            "你是数据分析师。要求：\n"
            "1. 涉及数值计算必须调用计算器工具，不要心算；\n"
            "2. 先说明分析思路再给结果；\n"
            "3. 结果用表格或要点呈现，保留合理精度；\n"
            "4. 指出数据的局限性和样本偏差。"
        ),
        "tools": ["calculator", "text_stats"],
        "builtin": True,
    },
]


@dataclass
class Skill:
    id: str
    name: str
    description: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    builtin: bool = False
    enabled: bool = True
    workspace_id: str = "default"


class SkillStore:
    skills: dict[str, Skill]

    def __init__(self) -> None:
        self.skills = {}

    def ensure_seed(self) -> None:
        for s in BUILTIN_SKILLS:
            if s["id"] not in self.skills:
                self.skills[s["id"]] = Skill(**s)

    def list_skills(self, workspace_id: str | None = None) -> list[Skill]:
        """内置技能全局可见，自定义技能只在所属工作区内可见。"""
        rows = list(self.skills.values())
        if workspace_id:
            rows = [s for s in rows if s.builtin or s.workspace_id == workspace_id]
        return rows

    def get(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)

    def add(self, name: str, description: str, system_prompt: str,
            tools: list[str], workspace_id: str = "default") -> Skill:
        skill = Skill(
            id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            system_prompt=system_prompt,
            tools=tools,
            workspace_id=workspace_id,
        )
        self.skills[skill.id] = skill
        return skill

    def update(self, skill_id: str, **fields: object) -> Skill | None:
        skill = self.skills.get(skill_id)
        if skill is None:
            return None
        for key, value in fields.items():
            if value is not None and hasattr(skill, key):
                setattr(skill, key, value)
        return skill

    def delete(self, skill_id: str) -> bool:
        skill = self.skills.get(skill_id)
        if skill is None or skill.builtin:
            return False
        del self.skills[skill_id]
        return True

    # ---- 持久化 ----
    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        payload = {k: asdict(v) for k, v in self.skills.items()}
        STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "SkillStore":
        store = cls()
        if STORE_PATH.exists():
            try:
                payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
                store.skills = {k: Skill(**v) for k, v in payload.items()}
            except Exception:  # noqa: BLE001 — 损坏则重建
                pass
        store.ensure_seed()
        return store


_skill_store: SkillStore | None = None
_lock = threading.Lock()


def get_skill_store() -> SkillStore:
    global _skill_store
    with _lock:
        if _skill_store is None:
            _skill_store = SkillStore.load()
        return _skill_store
