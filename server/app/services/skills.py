"""技能存储：技能是可复用的能力中间层（提示词模板 + 工具声明）。

专家挂载技能获得能力，智能体/应用引用专家。生产替换点：PostgreSQL。
"""

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger
from app.services.state import blob

logger = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STORE_PATH = DATA_DIR / "skills_store.json"
_STATE = blob("skills", STORE_PATH)

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
    # ---- 外部安装来源（source="local" 时为空） ----
    source: str = "local"          # local | github
    source_url: str = ""           # 可回跳的原始地址
    repo: str = ""                 # owner/repo
    ref: str = ""                  # 安装时选择的分支/标签
    commit: str = ""               # 解析出的 commit sha（版本）
    subpath: str = ""              # 技能在仓库内的目录
    author: str = ""
    license: str = ""
    files: list[dict] = field(default_factory=list)  # [{path, size}]
    installed_at: str = ""
    updated_at: str = ""
    # ---- 版本管理（semver + 快照回滚） ----
    version: str = "0.1.0"
    versions: list[dict] = field(default_factory=list)  # [{version, description, system_prompt, tools, commit, saved_at, note}]
    # 归属账号：'' = 预置技能（内置/GitHub 预装，全员可见）；其余为安装者 id
    owner_id: str = ""
    # 可见范围：private 仅安装者；workspace 同工作区可读（默认）；shared 跨工作区
    visibility: str = "private"


class SkillStore:
    skills: dict[str, Skill]

    def __init__(self) -> None:
        self.skills = {}

    def ensure_seed(self) -> None:
        for s in BUILTIN_SKILLS:
            if s["id"] not in self.skills:
                self.skills[s["id"]] = Skill(**s)

    def list_skills(
        self, workspace_id: str | None = None, user_id: str | None = None,
    ) -> list[Skill]:
        """内置技能全局可见；其余限所属工作区，给了 user_id 时再叠加归属与共享。"""
        rows = list(self.skills.values())
        if workspace_id:
            rows = [
                s for s in rows
                if s.builtin or s.workspace_id == workspace_id or s.visibility == "shared"
            ]
        if user_id is not None:
            rows = [
                s for s in rows
                if s.builtin or s.owner_id in ("", user_id) or s.visibility in ("workspace", "shared")
            ]
        return rows

    def visible(self, skill_id: str, workspace_id: str, user_id: str) -> Skill | None:
        """按 id 取技能并校验可见性；不可见一律当不存在。"""
        skill = self.skills.get(skill_id)
        if skill is None:
            return None
        if skill.builtin:
            return skill
        if skill.workspace_id != workspace_id and skill.visibility != "shared":
            return None
        if skill.owner_id in ("", user_id) or skill.visibility in ("workspace", "shared"):
            return skill
        return None

    def can_edit(self, skill: Skill, user_id: str, is_admin: bool = False) -> bool:
        """创建者能改自己的；预置技能（'' ）只有管理员能改。"""
        if skill.owner_id == user_id:
            return True
        return skill.owner_id == "" and is_admin

    def get(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)

    def add(self, name: str, description: str, system_prompt: str,
            tools: list[str], workspace_id: str = "default", owner_id: str = "",
            visibility: str = "private") -> Skill:
        skill = Skill(
            id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            system_prompt=system_prompt,
            tools=tools,
            workspace_id=workspace_id,
            owner_id=owner_id,
            visibility=visibility,
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

    def upsert(self, skill: Skill) -> bool:
        """按 id 幂等写入安装结果，返回是否新建。

        覆盖已有的同名安装时，保留用户的启用开关与首次安装时间。
        """
        existing = self.skills.get(skill.id)
        if existing is None:
            self.skills[skill.id] = skill
            return True
        skill.enabled = existing.enabled
        skill.installed_at = existing.installed_at or skill.installed_at
        self.skills[skill.id] = skill
        return False

    def delete(self, skill_id: str) -> bool:
        skill = self.skills.get(skill_id)
        if skill is None or skill.builtin:
            return False
        del self.skills[skill_id]
        return True

    # ---- 版本管理 ----
    MAX_VERSIONS = 20

    def snapshot(self, skill_id: str, note: str = "") -> dict | None:
        """把当前内容固化为一个新版本（patch 递增），返回快照记录。"""
        skill = self.skills.get(skill_id)
        if skill is None:
            return None
        parts = skill.version.split("-")[0].split(".")
        while len(parts) < 3:
            parts.append("0")
        try:
            patch = int(parts[2]) + 1
        except ValueError:
            patch = 1
        parts[2] = str(patch)
        skill.version = ".".join(parts)
        entry = {
            "version": skill.version,
            "name": skill.name,
            "description": skill.description,
            "system_prompt": skill.system_prompt,
            "tools": list(skill.tools),
            "commit": skill.commit,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": note,
        }
        skill.versions = (skill.versions + [entry])[-self.MAX_VERSIONS:]
        return entry

    def rollback(self, skill_id: str, version: str) -> Skill | None:
        """回滚到指定版本：恢复内容并追加一条回滚记录（版本号继续递增）。"""
        skill = self.skills.get(skill_id)
        if skill is None:
            return None
        snap = next((v for v in skill.versions if v.get("version") == version), None)
        if snap is None:
            return None
        skill.name = snap.get("name", skill.name)
        skill.description = snap.get("description", skill.description)
        skill.system_prompt = snap.get("system_prompt", skill.system_prompt)
        skill.tools = list(snap.get("tools", []))
        self.snapshot(skill_id, note=f"回滚自 {version}")
        return skill

    # ---- 持久化 ----
    def save(self) -> None:
        payload = {k: asdict(v) for k, v in self.skills.items()}
        _STATE.write(payload)

    @classmethod
    def load(cls) -> "SkillStore":
        store = cls()
        try:
            payload = _STATE.read() or {}
            store.skills = {k: Skill(**v) for k, v in payload.items()}
        except Exception as exc:  # noqa: BLE001 — 损坏则重建
            logger.warning("技能数据文件损坏，已重建：%s", exc)
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
