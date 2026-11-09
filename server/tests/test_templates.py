"""任务模板：可见性、使用计数、字段校验。

模板的坑不在存储，在语义：
- 默认必须是 private（任务描述里常带客户名与项目代号）；
- `use_count` 只能由"使用"这条路径增长，不能被编辑接口顺手改掉，
  否则"常用模板排前面"就变成了"谁改得勤谁排前面"。
"""

from __future__ import annotations

import pytest

from app.services import templates


@pytest.fixture(autouse=True)
def _clean():
    templates._table().delete_where("1=1")
    yield
    templates._table().delete_where("1=1")


def _make(**kw) -> dict:
    base = dict(
        workspace_id="ws1", owner_id="u1", name="每周竞品简报",
        prompt="汇总本周竞品动态，输出三段：产品更新、价格变化、值得跟的动作。",
    )
    base.update(kw)
    return templates.create_template(**base)


def test_create_defaults_to_private():
    tpl = _make()
    assert tpl["visibility"] == "private"
    assert tpl["use_count"] == 0


def test_private_template_hidden_from_teammate():
    tpl = _make()
    assert tpl["id"] in {t["id"] for t in templates.list_templates("ws1", "u1")}
    assert tpl["id"] not in {t["id"] for t in templates.list_templates("ws1", "u2")}


def test_workspace_template_visible_not_editable():
    tpl = _make(visibility="workspace")
    assert tpl["id"] in {t["id"] for t in templates.list_templates("ws1", "u2")}
    assert templates.can_edit(tpl, "u2") is False
    assert templates.can_edit(tpl, "u1") is True


def test_shared_template_visible_across_workspace():
    tpl = _make(visibility="shared")
    assert tpl["id"] in {t["id"] for t in templates.list_templates("ws2", "u9")}


def test_empty_name_and_prompt_rejected():
    with pytest.raises(ValueError):
        _make(name="  ")
    with pytest.raises(ValueError):
        _make(prompt="")


def test_bad_visibility_rejected():
    with pytest.raises(ValueError):
        _make(visibility="public")


def test_use_count_drives_ordering():
    a = _make(name="A")
    b = _make(name="B")
    templates.touch(b["id"])
    templates.touch(b["id"])
    order = [t["id"] for t in templates.list_templates("ws1", "u1")]
    assert order[0] == b["id"]
    assert order[1] == a["id"]


def test_update_cannot_touch_use_count():
    tpl = _make()
    templates.update_template(tpl["id"], {"use_count": 99, "name": "改名"})
    fresh = templates.get_template(tpl["id"])
    assert fresh["name"] == "改名"
    assert fresh["use_count"] == 0


def test_delete_is_idempotent():
    tpl = _make()
    assert templates.delete_template(tpl["id"]) is True
    assert templates.delete_template(tpl["id"]) is False


def test_payload_has_no_internal_fields():
    payload = templates.payload(_make())
    assert set(payload) == {
        "id", "name", "description", "prompt", "expert_id", "project",
        "tool_ids", "kb_ids", "visibility", "use_count", "owner_id",
        "workspace_id", "created_at", "updated_at",
    }
