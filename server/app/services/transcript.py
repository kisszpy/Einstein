"""会话导出：把一段对话渲染成 Markdown 或 JSON。

**为什么单独一个模块**：导出看着简单，栽在两件事上——
1. 文件名。中文标题直接塞进 `Content-Disposition` 会被浏览器按 latin-1 解码成乱码，
   必须同时给 ASCII 兜底名和 `filename*=UTF-8''…`；
2. 内容。模型答案里常有 `##`、列表、代码块，导出时既不能被当成标题层级打乱
   整篇结构，也不能转义到面目全非。这里选"只缩进引用块、不转义字符"——
   导出是给人读的，不是给机器再解析的。

引用卡片与工具步骤不在 `messages` 表里（只活在 SSE 流中），所以导出只有
问答正文。这是数据模型的边界，不是这里能补的：要连引用一起导出，得先让
对话把引用落库。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

# 文件名里不能出现的字符（Windows 比 POSIX 更严，按严的来）
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_MULTI_DASH = re.compile(r"-{2,}")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _human(ts: str) -> str:
    """库里存的是 UTC 带微秒的 ISO，导出的东西是给人看的，转本地时间到分钟。

    解析不了就原样返回：导出宁可难看，也不要因为一条脏时间戳整个失败。
    """
    raw = (ts or "").strip()
    if not raw:
        return "-"
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M")


def safe_filename(title: str, fallback: str = "conversation", *, limit: int = 60) -> str:
    """标题 → 可做文件名的片段：去非法字符、压缩连字符、截断到 limit。

    中文不做音译：`filename*=UTF-8''` 能保住原文，ASCII 兜底名另给。
    """
    name = _UNSAFE.sub("-", (title or "").strip())
    name = _MULTI_DASH.sub("-", name).strip("- .")
    if not name:
        return fallback
    return name[:limit].strip("- .") or fallback


def disposition(title: str, ext: str) -> str:
    """`attachment` 头：ASCII 兜底 + RFC 5987 UTF-8 原名。

    Firefox/Safari 用后者，老 IE/部分下载器用前者；两个都给，互不打架。
    """
    # 兜底名只留可打印 ASCII，替换成下划线后仍要能被老客户端接受
    stem = safe_filename(title).encode("ascii", "replace").decode("ascii").replace("?", "_")
    if not re.search(r"[A-Za-z0-9]", stem):
        # 纯中文标题会变成一串下划线，等于没有名字；给个带日期的通用名。
        # 判据要看主干而不是整串，否则 "____.md" 里的 "md" 会让我们以为它有名字。
        stem = f"transcript-{datetime.now().strftime('%Y%m%d')}"
    ascii_name = f"{stem}.{ext}"
    utf8_name = quote(f"{safe_filename(title)}.{ext}", safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"


def _role_label(role: str) -> str:
    return {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}.get(
        role, role or "未知"
    )


def to_markdown(conv, *, exported_at: str | None = None) -> str:
    """整段对话 → Markdown。

    模型答案整体包在引用块里：它在 Markdown 里是"引用"，不与文档自身的
    标题层级竞争，长文也不会把目录结构带歪。用户输入保持原样——那才是
    用户真正说的话，缩进反而失真。
    """
    exported_at = exported_at or _now()
    messages = list(conv.messages or [])
    lines: list[str] = [f"# {conv.title or '未命名会话'}", ""]
    lines.append(f"- 会话 ID：`{conv.id}`")
    lines.append(f"- 创建：{_human(conv.created_at)}")
    lines.append(f"- 最后更新：{_human(conv.updated_at)}")
    lines.append(f"- 消息数：{len(messages)}")
    lines.append(f"- 导出时间：{exported_at}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not messages:
        lines.append("_（这段会话没有消息）_")
        lines.append("")

    for msg in messages:
        stamp = f" · {_human(msg.created_at)}" if getattr(msg, "created_at", "") else ""
        lines.append(f"## {_role_label(msg.role)}{stamp}")
        lines.append("")
        body = (msg.content or "").strip("\n")
        if not body:
            lines.append("_（空）_")
        elif msg.role == "assistant":
            quoted = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in body.split("\n"))
            lines.append(quoted)
        else:
            lines.append(body)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def to_json(conv, *, exported_at: str | None = None) -> str:
    """结构化导出：字段名与 `GET /conversations/{id}` 对齐，方便脚本二次处理。"""
    payload = {
        "id": conv.id,
        "title": conv.title,
        "workspace_id": getattr(conv, "workspace_id", ""),
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "archived": bool(getattr(conv, "archived", False)),
        "pinned": bool(getattr(conv, "pinned", False)),
        "message_count": len(conv.messages or []),
        "exported_at": exported_at or _now(),
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in (conv.messages or [])
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


RENDERERS = {"md": ("md", "text/markdown; charset=utf-8", to_markdown),
             "markdown": ("md", "text/markdown; charset=utf-8", to_markdown),
             "json": ("json", "application/json; charset=utf-8", to_json)}
