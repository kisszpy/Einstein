"""可见性矩阵：private 只有自己、workspace 同空间可读、shared 跨空间可读。

v0.7.0 之前 list_* 一律按 owner 过滤，`visibility` 字段只是躺在表里；
这里把三档语义钉死，避免以后又变成"字段在、判定不在"。
"""

from __future__ import annotations

from app.services.experts import ExpertStore
from app.services.repositories.kb import JsonKbRepository
from app.services.skills import SkillStore


def test_kb_visibility_matrix(tmp_path):
    repo = JsonKbRepository(tmp_path / "kb.json")
    private = repo.create_kb("私有库", workspace_id="ws1", owner_id="u1")
    teammate = repo.create_kb("同事可见", workspace_id="ws1", owner_id="u1", visibility="workspace")
    shared = repo.create_kb("跨空间", workspace_id="ws2", owner_id="u1", visibility="shared")

    visible = {k.id for k in repo.list_kbs("ws1", "u2")}
    assert private.id not in visible
    assert teammate.id in visible
    assert shared.id in visible

    # 按 id 访问同样要拦：列表看不到、直连却拿到，等于没隔离
    assert repo.owns(private.id, "ws1", "u2") is False
    assert repo.owns(teammate.id, "ws1", "u2") is True
    assert repo.owns(shared.id, "ws1", "u2") is True


def test_kb_owner_always_sees_own(tmp_path):
    repo = JsonKbRepository(tmp_path / "kb.json")
    kb = repo.create_kb("私有库", workspace_id="ws1", owner_id="u1")
    assert kb.id in {k.id for k in repo.list_kbs("ws1", "u1")}
    assert repo.owns(kb.id, "ws1", "u1") is True


def test_expert_visibility_matrix():
    store = ExpertStore()
    private = store.add(name="私有", owner_id="u1", workspace_id="ws1")
    teammate = store.add(name="共享", owner_id="u1", workspace_id="ws1", visibility="workspace")

    visible = {e.id for e in store.list_experts("ws1", "u2")}
    assert private.id not in visible
    assert teammate.id in visible
    assert store.visible(private.id, "ws1", "u2") is None
    assert store.visible(teammate.id, "ws1", "u2") is not None

    # 共享只给读，不给改
    assert store.can_edit(teammate, "u2") is False


def test_skill_visibility_matrix():
    store = SkillStore()
    private = store.add("私有技能", "", "prompt", [], workspace_id="ws1", owner_id="u1")
    teammate = store.add(
        "共享技能", "", "prompt", [], workspace_id="ws1", owner_id="u1", visibility="workspace",
    )

    visible = {s.id for s in store.list_skills("ws1", "u2")}
    assert private.id not in visible
    assert teammate.id in visible
    assert store.visible(private.id, "ws1", "u2") is None
    assert store.visible(teammate.id, "ws1", "u2") is not None
