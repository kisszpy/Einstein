"""把工作区里已安装的 impeccable 技能接进润色层。

技能不是一份可直接当提示词用的文档：`SKILL.md` 是路由，正文按命令分派到
`reference/<command>.md`，并且明确要求「编辑前先读 craft-floor.md」。所以这里
照它的协议走一遍——读 SKILL.md，按本轮命令（polish）取 reference/polish.md，
再叠加 craft-floor.md——而不是把整包 58 个文件塞进提示词。

源目录是 `<workspace>/.agent/skills/<slug>/`：技能由平台的技能系统安装、发布，
用户可以在技能管理里升级/回滚，这里只读，不改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

SKILL_REPO = "pbakaus/impeccable"
# 技能的命令表里，润色命令叫 polish；它自带的品质地板是每次编辑前必读
COMMAND = "polish"
ALWAYS = ("reference/craft-floor.md",)
# new-work.md 是 65KB 的整条建档流程；选视觉方向只需要「挑选方向 → 定案」那两节，
# 整篇塞进提示词既贵又会把模型带去写 PRODUCT.md。按标题切片，版本仍以技能为准。
_SECTIONS = {"new-work": ("## 3.", "## 5.")}

_FRONTMATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.S)
_PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")


@dataclass
class SkillContext:
    name: str
    slug: str
    commit: str
    command: str
    system: str
    files: tuple[str, ...]

    def payload(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "version": self.commit[:12],
            "command": self.command,
            "files": list(self.files),
            "chars": len(self.system),
        }


def resolve(workspace_id: str, command: str = COMMAND) -> SkillContext | None:
    """找到已安装的技能并拼出系统提示词。没装就返回 None（调用方回落）。"""
    try:
        from app.services import skill_publisher
        from app.services.skills import get_skill_store

        store = get_skill_store()
        skill = next(
            (s for s in store.list_skills(workspace_id, None) if s.repo == SKILL_REPO),
            None,
        )
        if skill is None or not skill.enabled:
            return None
        root = skill_publisher.skills_dir(workspace_id) / skill_publisher.slug_of(skill)
        body = _read(root / "SKILL.md")
        if not body:
            return None
        files = ["SKILL.md"]
        sections = [_strip(body)]
        for rel in (f"reference/{command}.md", *ALWAYS):
            text = _read(root / rel)
            if text:
                files.append(rel)
                sections.append(f"<!-- {rel} -->\n{_section(_strip(text), command)}")
        return SkillContext(
            name=skill.name,
            slug=skill_publisher.slug_of(skill),
            commit=skill.commit,
            command=command,
            system="\n\n---\n\n".join(sections),
            files=tuple(files),
        )
    except Exception as exc:  # noqa: BLE001 - 技能读不到只是回落，不该中断生成
        logger.warning("读取 impeccable 技能失败：%s", exc)
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _strip(text: str) -> str:
    """去 frontmatter；把只对 agent runtime 有意义的占位符收成普通路径。"""
    text = _FRONTMATTER.sub("", text, count=1)
    return _PLACEHOLDER.sub("impeccable", text).strip()


def _section(text: str, command: str) -> str:
    """按技能文件自己的标题切出本轮需要的那两节；切不到就用全文。"""
    marks = _SECTIONS.get(command)
    if not marks:
        return text
    start, end = marks
    begin = text.find(start)
    if begin < 0:
        return text
    finish = text.find(end, begin + len(start))
    return text[begin:finish].strip() if finish > begin else text[begin:].strip()


__all__ = ["SkillContext", "resolve", "SKILL_REPO", "COMMAND"]