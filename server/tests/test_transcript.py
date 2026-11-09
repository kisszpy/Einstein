"""会话导出：渲染、文件名、Content-Disposition。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import transcript


def _conv(title="死锁为什么发生", messages=None, cid="c1"):
    return SimpleNamespace(
        id=cid, title=title, created_at="2026-11-09T10:00:00+08:00",
        updated_at="2026-11-09T10:05:00+08:00", workspace_id="default",
        archived=False, pinned=False,
        messages=messages if messages is not None else [
            SimpleNamespace(role="user", content="什么是死锁？", created_at="10:00"),
            SimpleNamespace(
                role="assistant",
                content="死锁是互相等待。\n\n常见四个必要条件：\n- 互斥\n- 占有并等待",
                created_at="10:01",
            ),
        ],
    )


def test_markdown_has_header_and_roles():
    md = transcript.to_markdown(_conv())
    assert md.startswith("# 死锁为什么发生")
    assert "会话 ID：`c1`" in md
    assert "## 用户 · 10:00" in md
    assert "## 助手 · 10:01" in md
    # 用户的问句保持原样，不被缩进
    assert "\n什么是死锁？\n" in md


def test_assistant_body_is_quoted_so_headings_dont_steal_structure():
    md = transcript.to_markdown(_conv())
    assert "> 死锁是互相等待。" in md
    # 模型答案里的列表项也一起进引用块，不会打断文档自身的层级
    assert "> - 互斥" in md
    assert "\n## 死锁" not in md


def test_empty_conversation_still_renders():
    md = transcript.to_markdown(_conv(messages=[]))
    assert "这段会话没有消息" in md


def test_json_roundtrip():
    payload = json.loads(transcript.to_json(_conv()))
    assert payload["id"] == "c1"
    assert payload["message_count"] == 2
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["exported_at"]


def test_iso_timestamps_are_rendered_local_and_short():
    md = transcript.to_markdown(_conv())
    # 库里是 UTC 带微秒的 ISO，导出应当是"到分钟"的本地时间，不再是原始串
    assert "T15:45:58" not in md
    assert "- 创建：20" in md


def test_unparsable_timestamp_falls_back_to_raw():
    assert transcript._human("不是时间") == "不是时间"
    assert transcript._human("") == "-"


def test_ascii_fallback_name_is_not_all_underscores():
    header = transcript.disposition("死锁排查", "md")
    ascii_part = header.split("filename=")[1].split(";")[0].strip('"')
    assert ascii_part.startswith("transcript-")


def test_safe_filename_strips_illegal_chars():
    name = transcript.safe_filename('报告/草稿:v1?* "终"')
    for ch in '\\/:*?"<>|':
        assert ch not in name
    assert name.startswith("报告-草稿")


def test_safe_filename_falls_back_and_truncates():
    assert transcript.safe_filename("   ") == "conversation"
    assert len(transcript.safe_filename("长" * 200)) == 60


def test_disposition_has_ascii_and_utf8_names():
    header = transcript.disposition("死锁为什么发生", "md")
    assert header.startswith("attachment;")
    assert "filename=" in header and "filename*=UTF-8''" in header
    # ASCII 兜底名里不能有非 ASCII 字节
    ascii_part = header.split("filename=")[1].split(";")[0]
    ascii_part.encode("ascii")
    assert "死锁" not in ascii_part
