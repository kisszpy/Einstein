"""技能发布器：SkillStore（DB，真源）→ 工作区 skills/ 目录（deepagents 格式）。

deepagents 的 SkillsMiddleware 在每次任务线程首次运行时从后端扫描
skills/ 目录下的 SKILL.md（YAML frontmatter：name + description，正文为
方法论）。本模块在每个智能体任务开始时做一次全量同步，因此技能的
创建/编辑/回滚/停用/删除即时生效（热加载），不需要任何重启或钩子。

SKILL.md 规范要求 name 仅小写字母/数字/连字符（≤64 字符），中文技能名
放不进去——目录与 name 统一用 ``skill-<id>``，中文原名写进 description。
"""

import json
import shutil
from pathlib import Path

from app.services.capabilities.sandbox import workspace_root
from app.services.skills import Skill, SkillStore, get_skill_store

# 技能同步目录：**不进用户的项目命名空间**。
#
# 早先它是工作区根下的 `skills/`，和用户项目平级——于是「目录即项目」的列表
# 把技能目录当成了一个项目，用户点进去看到的是平台自己的技能文件。运行时
# 产物一律收进 `.agent/` 这个点开头的隐藏目录：用户看到的根目录里只有他自己
# 创建的项目，模型侧仍可按 `/.agent/skills/` 读取（渐进披露不变）。
SKILLS_SUBDIR = ".agent/skills"


def slug_of(skill: Skill) -> str:
    """SKILL.md 的 name / 目录名：规范合法且与 skill.id 一一对应。"""
    return f"skill-{skill.id}"[:64]


def skills_dir(workspace_id: str) -> Path:
    return workspace_root(workspace_id) / SKILLS_SUBDIR


def _frontmatter(skill: Skill, description: str) -> str:
    # description 用 JSON 双引号标量：GitHub 技能的描述里普遍含「: 」等
    # YAML 特殊序列，裸写会解析失败（mapping values are not allowed here）
    lines = [
        "---",
        f"name: {slug_of(skill)}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
    ]
    if skill.tools:
        lines.append(f"allowed-tools: {' '.join(skill.tools)}")
    lines.append("---")
    return "\n".join(lines)


def _publish_one(skill: Skill, target: Path) -> None:
    """把单个技能写成 <target>/SKILL.md + 参考文件。"""
    from app.services.skill_installer import SKILLS_DIR

    target.mkdir(parents=True, exist_ok=True)
    # description 里带上中文原名：模型看清单时看到的是人类可读的名字
    display = skill.name.strip() or skill.id
    desc = f"{display}：{skill.description.strip()}" if skill.description.strip() else display
    desc = desc.replace("\n", " ")[:1024]
    body = skill.system_prompt.strip()
    if skill.files:
        names = "、".join(f["path"] for f in skill.files)
        body += (
            f"\n\n【参考文件】本技能附带以下资料（相对本 SKILL.md 所在目录，"
            f"需要时用 read_file 按需读取，不要一次全部读入）：{names}"
        )
    # 安装包自带的参考文件随目录拷贝；先拷再写，保证我们的 frontmatter
    # 永远是最终态（安装包可能自带同名 SKILL.md，覆盖会让 name 与目录
    # 不匹配，破坏 spec 校验与 pinned_skills 匹配）
    src_root = (SKILLS_DIR / skill.id).resolve()
    if src_root.is_dir():
        for src in sorted(src_root.rglob("*")):
            if src.is_file() and src.name.lower() != "skill.md":
                rel = src.relative_to(src_root)
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    (target / "SKILL.md").write_text(
        _frontmatter(skill, desc) + "\n\n" + body + "\n", encoding="utf-8"
    )


def sync_skills(
    ws: str, store: SkillStore | None = None, owner_id: str | None = None,
) -> list[str]:
    """把工作区内 enabled 的技能同步到 <workspace>/.agent/skills/，返回已发布的 name 清单。

    全量对比：DB 里不存在或已停用的技能，其目录会被清掉。同步是幂等的，
    未变化的技能重写一遍内容（数量级小，毫秒级开销换一致性，值得）。
    """
    store = store or get_skill_store()
    root = skills_dir(ws)
    root.mkdir(parents=True, exist_ok=True)

    published: list[str] = []
    keep: set[str] = set()
    # owner_id 给定时只发布「预置 + 本人」的技能：工作区 skills/ 目录是共享的，
    # 把别人的自定义技能写进去等于文件级越权
    for skill in store.list_skills(ws, owner_id):
        if not skill.enabled or not skill.system_prompt.strip():
            continue
        slug = slug_of(skill)
        keep.add(slug)
        _publish_one(skill, root / slug)
        published.append(slug)

    # 清掉停用/删除/无正文的残留目录
    for entry in root.iterdir():
        if entry.is_dir() and entry.name not in keep:
            shutil.rmtree(entry, ignore_errors=True)
        elif entry.is_file() and entry.name not in keep:
            entry.unlink(missing_ok=True)
    return published


def pinned_slugs(ws: str, skill_ids: list[str], store: SkillStore | None = None) -> list[str]:
    """把请求里点名的 skill_ids 翻译成 SkillsMiddleware 的 pinned_skills。

    只保留「存在 + enabled + 本工作区可见」的技能；无效项静默丢弃
    （与 kb_ids 同一防探测原则）。store 传入可复用测试内存库。
    """
    store = store or get_skill_store()
    out: list[str] = []
    for sid in skill_ids:
        skill = store.get(str(sid).strip())
        if skill is None or not skill.enabled or not skill.system_prompt.strip():
            continue
        if not skill.builtin and skill.workspace_id != ws:
            continue
        out.append(slug_of(skill))
    return out


def export_manifest(ws: str) -> list[dict]:
    """调试/测试用：列出当前已发布技能的 name 与描述。"""
    root = skills_dir(ws)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for md in sorted(root.glob("*/SKILL.md")):
        meta = {"name": md.parent.name, "description": "", "body_chars": 0}
        try:
            text = md.read_text(encoding="utf-8")
            meta["description"] = text.split("description: ", 1)[1].split("\n", 1)[0]
            meta["body_chars"] = len(text) - text.split("---", 2)[2].__len__() if text.count("---") >= 2 else len(text)
        except Exception:  # noqa: BLE001 — 调试用，读不出就跳过字段
            pass
        out.append(meta)
    return out


def _debug_dump(ws: str) -> str:  # pragma: no cover
    return json.dumps(export_manifest(ws), ensure_ascii=False, indent=2)
