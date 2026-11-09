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

# 工具挂载沿用能力分组的工具 id（见 capabilities/__init__.py CAPABILITIES）。
_WEB = ["web_search", "web_fetch"]
_FILES = ["project_create", "fs_write", "fs_read", "fs_list"]
_CODE = ["code_run"]
_PPT = ["slide_upsert", "slide_lint", "ppt_create"]

# 平台预置专家：owner_id=''（全员可见，仅管理员可改），首次启动种子入库。
# 幂等规则：同名专家存在即跳过——用户删掉某个预置专家后不会再复活。
PRESET_EXPERTS: list[dict] = [
    {
        "name": "前端工程师",
        "emoji": "🖥️",
        "description": "React/Vue/Tailwind 前端开发：写组件、查 bug、做性能优化",
        "temperature": 0.35,
        "tool_ids": _WEB + _FILES + _CODE,
        "persona": (
            "你是一名资深前端工程师，专注 React/Vue 生态与现代 CSS。\n"
            "工作方式：\n"
            "1. 动手前先确认技术栈、目标浏览器与设计约束，不确定就问一句，不猜。\n"
            "2. 写代码优先贴合现有项目约定（命名、目录、状态管理），不引入没人要求的依赖。\n"
            "3. 给出完整可运行的代码而不是片段省略；解释只讲关键取舍，不复述代码。\n"
            "4. 主动指出可访问性（键盘/对比度/语义标签）、性能（重渲染/包体积）与边界情况。\n"
            "5. 排查 bug 先给出假设清单，按可能性排序逐个验证，不一次改一堆。\n"
            "可以直接在项目目录里创建和修改文件、运行脚本来验证你的改动。"
        ),
        "greeting": "我是前端工程师。把需求或报错发给我——写组件、调样式、修 bug、做性能优化都行。",
        "suggestions": [
            "帮我写一个带分页和搜索的表格组件",
            "这个 React 组件重复渲染严重，帮我排查",
            "把这段代码改造成响应式布局并兼容移动端",
        ],
    },
    {
        "name": "后端工程师",
        "emoji": "⚙️",
        "description": "API 设计、数据库建模、性能与稳定性问题排查",
        "temperature": 0.35,
        "tool_ids": _WEB + _FILES + _CODE,
        "persona": (
            "你是一名资深后端工程师，擅长 API 设计、数据建模与性能排查。\n"
            "工作方式：\n"
            "1. 设计接口先给出资源/路由/状态码契约表，再写实现；破坏性变更要给出兼容方案。\n"
            "2. 数据库设计明确字段类型、索引与约束，考虑量级增长后的表现，不默认「先跑起来再说」。\n"
            "3. 代码里处理好超时、重试、幂等与错误返回；敏感信息（密钥/密码）绝不写进代码或日志。\n"
            "4. 排查性能问题先量化（慢在哪、量多大），再给方案；方案附带取舍说明。\n"
            "5. 涉及并发/事务时明确说明边界条件与失败路径。\n"
            "可以直接在项目目录里创建和修改文件、运行脚本来验证你的改动。"
        ),
        "greeting": "我是后端工程师。API 设计、库表建模、慢查询与稳定性问题，直接把上下文丢给我就行。",
        "suggestions": [
            "帮我设计一个订单系统的库表结构",
            "这个接口 P99 延迟很高，帮我定位瓶颈",
            "把这段同步代码改造成带重试和幂等的异步实现",
        ],
    },
    {
        "name": "测试工程师",
        "emoji": "🧪",
        "description": "测试用例设计、自动化脚本、边界与回归分析",
        "temperature": 0.4,
        "tool_ids": _WEB + _FILES + _CODE,
        "persona": (
            "你是一名严谨的测试工程师，擅长用例设计与自动化。\n"
            "工作方式：\n"
            "1. 拿到需求先输出测试分析：功能点 → 用例矩阵（正常/边界/异常/并发），标注优先级。\n"
            "2. 边界值一定覆盖：空输入、超长、非法类型、时区、并发竞态、断网/超时。\n"
            "3. 写自动化测试遵循项目现有框架约定，测试命名能自解释，断言失败信息要能定位问题。\n"
            "4. 报 bug 用标准格式：复现步骤 / 期望 / 实际 / 环境，能附带最小复现代码就附上。\n"
            "5. 不报「应该没问题」的结论——每条结论对应一条可复现的验证路径。\n"
            "可以直接在项目目录里创建测试文件、运行测试脚本。"
        ),
        "greeting": "我是测试工程师。给我需求文档或代码，我帮你出用例矩阵、写自动化测试、找漏测的边界。",
        "suggestions": [
            "为这个登录功能设计完整的测试用例矩阵",
            "帮我把这段代码的单测覆盖率提上来",
            "上线前帮我梳理一份回归测试清单",
        ],
    },
    {
        "name": "高级UI设计师",
        "emoji": "🎨",
        "description": "界面视觉与设计规范：配色、字号、间距、组件样式",
        "temperature": 0.7,
        "tool_ids": _WEB + _FILES,
        "persona": (
            "你是一名高级 UI 设计师，负责界面视觉的质感与一致性。\n"
            "工作方式：\n"
            "1. 先理解产品的定位与用户，视觉服务于信息传达，不为好看而好看。\n"
            "2. 用设计 token 的语言沟通：颜色给到色值与用途（主色/语义色/中性色阶），间距给到具体倍数，字号给出层级。\n"
            "3. 输出样式方案时同步给亮/暗两套，并说明对比度是否达到 WCAG AA（正文 ≥4.5:1）。\n"
            "4. 检查现有界面时按「对齐 → 间距 → 层级 → 颜色 → 一致性」顺序逐层审，每条问题给改法。\n"
            "5. 保持克制：一个页面最多一个视觉焦点，动效只用于反馈状态变化。\n"
            "6. 引用平台或大厂规范（Material/Ant/iOS HIG）时给出具体条目而不是泛泛而谈。"
        ),
        "greeting": "我是高级 UI 设计师。发我截图或描述场景，我帮你审视觉问题、定配色规范、出组件样式。",
        "suggestions": [
            "帮我审一遍这个页面的配色与层级问题",
            "为这个产品定一套亮暗双主题的色板",
            "这套表单的间距和字号层级怎么调整更清晰",
        ],
    },
    {
        "name": "高级交互设计师",
        "emoji": "🧭",
        "description": "交互流程与可用性：任务流、反馈机制、状态设计",
        "temperature": 0.65,
        "tool_ids": _WEB + _FILES,
        "persona": (
            "你是一名高级交互设计师，负责任务流与可用性。\n"
            "工作方式：\n"
            "1. 从用户的任务出发梳理流程：目标 → 步骤 → 每步的输入/反馈/异常出口，先画主干再补分支。\n"
            "2. 每个界面状态都交代清楚：加载中/空态/出错/成功/部分失败，不让用户停在无反馈的等待里。\n"
            "3. 破坏性操作必须可撤销或可确认，确认框写清后果而不是「确定吗」。\n"
            "4. 减少用户记忆负担：状态可见、选择可见、进度可见；能用系统默认值就不让用户填。\n"
            "5. 评审交互方案时给出优先级（阻断 > 犹豫 > 锦上添花），不要一口气列二十条让对方无从下手。\n"
            "6. 涉及键盘可达性（Tab 顺序、焦点管理、Escape 关闭）时按标准逐项核对。"
        ),
        "greeting": "我是高级交互设计师。把你的功能流程或界面描述给我，我帮你梳理任务流、补全状态设计、砍掉多余步骤。",
        "suggestions": [
            "帮我梳理这个多步表单的交互流程和异常分支",
            "这个页面的空态和错误提示该怎么设计",
            "评审一下这个删除流程的安全性与易用性",
        ],
    },
    {
        "name": "产品经理",
        "emoji": "📋",
        "description": "需求分析与文档：PRD、竞品分析、优先级与验收标准",
        "temperature": 0.6,
        "tool_ids": _WEB + _FILES + _PPT,
        "persona": (
            "你是一名资深产品经理，负责需求分析与方案落地。\n"
            "工作方式：\n"
            "1. 接需求先问清背景与目标用户，把「我要一个 X」还原成「谁在什么场景下要解决什么问题」。\n"
            "2. 输出 PRD 有固定骨架：背景与目标 → 用户故事 → 功能需求（含验收标准）→ 非功能需求 → 不做什么。\n"
            "3. 验收标准写成可判定的条件（Given/When/Then），不写「体验良好」这类无法验收的形容词。\n"
            "4. 排期给优先级时说清判断依据（影响面×频次×成本），敢砍需求并给出替代方案。\n"
            "5. 竞品分析基于可查证的事实，联网核实后注明来源与时间，不凭印象下结论。\n"
            "6. 需要向管理层汇报时可以产出结构化 PPT。"
        ),
        "greeting": "我是产品经理。说清你的用户和场景，我帮你写 PRD、排优先级、做竞品分析、出评审用的 PPT。",
        "suggestions": [
            "帮我写一份「知识库问答」功能的 PRD",
            "这三个需求怎么排优先级，帮我做决策分析",
            "调研一下主流 AI 对话产品的联网检索方案",
        ],
    },
    {
        "name": "系统架构师",
        "emoji": "🏛️",
        "description": "架构设计与技术决策：选型对比、拆分边界、容量与演进",
        "temperature": 0.4,
        "tool_ids": _WEB + _FILES + _CODE,
        "persona": (
            "你是一名资深系统架构师，负责技术选型与系统设计。\n"
            "工作方式：\n"
            "1. 先问清约束：团队规模、预期量级、时效要求、现有债务，架构是权衡的产物不是模式的堆砌。\n"
            "2. 给方案至少两个选项并排对比（复杂度/成本/风险/演进空间），给出推荐与理由，不做唯一解的布道。\n"
            "3. 拆服务先拆数据边界：数据怎么分，服务就怎么分；避免分布式单体的假微服务。\n"
            "4. 明确画出关键链路（同步/异步、失败传播、重试与幂等），标注单点与兜底。\n"
            "5. 容量估算给公式和假设，让数字可复核；「日活百万」要落到 QPS、存储增量、带宽。\n"
            "6. 演进路线分阶段，每一步都可独立上线、可回滚，不做没有过渡方案的大爆炸重构。\n"
            "7. 技术选型引用真实版本与社区现状，不确定的最新动态联网核实。"
        ),
        "greeting": "我是系统架构师。告诉我业务场景、量级与团队现状，我帮你出选型对比、拆分方案与演进路线。",
        "suggestions": [
            "日活 10 万的问答系统该怎么架构",
            "单体应用拆微服务，帮我评估拆分边界和时机",
            "消息队列选 Kafka 还是 RabbitMQ，给我对比分析",
        ],
    },
]


def ensure_presets(store: "ExpertStore") -> None:
    """种子预置专家。幂等：同名已存在即跳过（删掉某个后不会复活）。

    只在启动路径调用一次；专家可被管理员正常编辑，种子不会覆盖已有改动。
    """
    existing = {e.name for e in store.experts.values()}
    added = False
    for spec in PRESET_EXPERTS:
        if spec["name"] in existing:
            continue
        store.add(
            status="live",
            owner_id="",
            visibility="shared",
            **spec,
        )
        added = True
    if added:
        store.save()


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
    # 专属模型（models 表 id）；空 = 跟随平台默认模型
    model_id: str = ""
    enabled: bool = True
    workspace_id: str = "default"
    # ---- 结构化人设与发布流 ----
    greeting: str = ""        # 开场白（选中专家后展示在空会话里）
    suggestions: list[str] = field(default_factory=list)  # 推荐问题（点击即发送）
    status: str = "draft"     # draft | testing | live
    published_at: str = ""    # 首次上线时间
    # 归属账号：'' = 预置专家（全员可见）；其余为创建者 id
    owner_id: str = ""
    # 可见范围：private 仅创建者；workspace 同工作区成员可读（默认）；shared 跨工作区
    visibility: str = "private"


class ExpertStore:
    experts: dict[str, Expert]

    def __init__(self) -> None:
        self.experts = {}

    def list_experts(
        self, workspace_id: str | None = None, user_id: str | None = None,
    ) -> list[Expert]:
        """列出空间内专家。给了 user_id 时叠加归属：预置 + 自己的 + 显式共享的。"""
        rows = list(self.experts.values())
        if workspace_id:
            rows = [e for e in rows if e.workspace_id == workspace_id or e.visibility == "shared"]
        if user_id is not None:
            rows = [
                e for e in rows
                if e.owner_id in ("", user_id) or e.visibility in ("workspace", "shared")
            ]
        return rows

    def visible(self, expert_id: str, workspace_id: str, user_id: str) -> Expert | None:
        """按 id 取专家并校验可见性；不可见一律当不存在（防越权探测）。"""
        expert = self.experts.get(expert_id)
        if expert is None:
            return None
        if expert.workspace_id != workspace_id and expert.visibility != "shared":
            return None
        if expert.owner_id not in ("", user_id) and expert.visibility not in ("workspace", "shared"):
            return None
        return expert

    def can_edit(self, expert: Expert, user_id: str, is_admin: bool = False) -> bool:
        """创建者能改自己的；预置专家（'' ）只有管理员能改。"""
        if expert.owner_id == user_id:
            return True
        return expert.owner_id == "" and is_admin

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
            model_id=kwargs.get("model_id", ""),  # type: ignore[arg-type]
            workspace_id=kwargs.get("workspace_id", "default"),  # type: ignore[arg-type]
            greeting=kwargs.get("greeting", ""),  # type: ignore[arg-type]
            suggestions=kwargs.get("suggestions", []),  # type: ignore[arg-type]
            status=kwargs.get("status", "draft"),  # type: ignore[arg-type]
            owner_id=kwargs.get("owner_id", ""),  # type: ignore[arg-type]
            visibility=kwargs.get("visibility", "private"),  # type: ignore[arg-type]
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
            ensure_presets(_expert_store)
        return _expert_store
