"""知识库权限：读/写分离 + 库级授权。

两个易错点，各写一组用例钉住：
- **能读不等于能写**：workspace 可见的库全空间可读，但只有创建者能改；
- **授权是叠加层**：private 库被授权后能读，且授权不会顺带给出写权限。
"""

from __future__ import annotations

import pytest

from app.services import kb_access
from app.services.repositories.kb import JsonKbRepository


@pytest.fixture(autouse=True)
def _clean():
    for kb_id, uid in [(r.get("kb_id"), r.get("user_id")) for r in kb_access._all()]:
        kb_access.revoke(kb_id, uid)
    yield
    for kb_id, uid in [(r.get("kb_id"), r.get("user_id")) for r in kb_access._all()]:
        kb_access.revoke(kb_id, uid)


class _Store:
    """只暴露 get_kb 的最小替身，权限判定不需要真的落盘。"""

    def __init__(self) -> None:
        self.repo = JsonKbRepository.__new__(JsonKbRepository)

    def get_kb(self, kb_id):  # noqa: ANN001
        return self.repo.get_kb(kb_id)


@pytest.fixture()
def store(tmp_path):
    repo = JsonKbRepository(tmp_path / "kb.json")
    stub = _Store()
    stub.repo = repo
    return stub, repo


def test_owner_has_write(store):
    stub, repo = store
    kb = repo.create_kb("我的库", workspace_id="ws1", owner_id="u1")
    assert kb_access.access(stub, kb.id, "ws1", "u1") == "write"


def test_workspace_visible_is_read_not_write(store):
    stub, repo = store
    kb = repo.create_kb("共享给空间", workspace_id="ws1", owner_id="u1", visibility="workspace")
    # 同事能读
    assert kb_access.access(stub, kb.id, "ws1", "u2") == "read"
    # 但不能改 —— 这就是"设了可见性就等于给了写权限"的老问题
    assert kb_access.access(stub, kb.id, "ws1", "u2") != "write"


def test_private_kb_invisible_without_grant(store):
    stub, repo = store
    kb = repo.create_kb("私有", workspace_id="ws1", owner_id="u1")
    assert kb_access.access(stub, kb.id, "ws1", "u2") is None


def test_grant_opens_private_kb_read_only(store):
    stub, repo = store
    kb = repo.create_kb("财务制度", workspace_id="ws1", owner_id="u1")
    kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u2", role="read", granted_by="u1")
    assert kb_access.access(stub, kb.id, "ws1", "u2") == "read"
    # 授权读不给写
    assert kb_access.access(stub, kb.id, "ws1", "u2") != "write"
    assert kb.id in kb_access.granted_kb_ids("u2")


def test_grant_write_upgrades(store):
    stub, repo = store
    kb = repo.create_kb("协作库", workspace_id="ws1", owner_id="u1")
    kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u2", role="write", granted_by="u1")
    assert kb_access.access(stub, kb.id, "ws1", "u2") == "write"


def test_regrant_is_update_not_duplicate(store):
    stub, repo = store
    kb = repo.create_kb("库", workspace_id="ws1", owner_id="u1")
    first = kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u2", role="read")
    second = kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u2", role="write")
    assert first["id"] == second["id"]
    rows = kb_access.list_grants(kb.id)
    assert len(rows) == 1
    assert rows[0]["role"] == "write"


def test_revoke_removes_access(store):
    stub, repo = store
    kb = repo.create_kb("库", workspace_id="ws1", owner_id="u1")
    kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u2", role="read")
    assert kb_access.revoke(kb.id, "u2") is True
    assert kb_access.access(stub, kb.id, "ws1", "u2") is None
    assert kb_access.revoke(kb.id, "u2") is False


def test_shared_kb_is_read_across_workspace(store):
    stub, repo = store
    kb = repo.create_kb("公开", workspace_id="ws2", owner_id="u1", visibility="shared")
    assert kb_access.access(stub, kb.id, "ws1", "u3") == "read"


def test_preset_kb_never_writable_without_grant(store):
    stub, repo = store
    kb = repo.create_kb("预置库", workspace_id="ws1", owner_id="")
    # 预置库没人"拥有"，默认只能读
    assert kb_access.access(stub, kb.id, "ws1", "u9") == "read"
    kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u9", role="write")
    assert kb_access.access(stub, kb.id, "ws1", "u9") == "write"


def test_unknown_kb_returns_none(store):
    stub, _ = store
    assert kb_access.access(stub, "nope", "ws1", "u1") is None


def test_invalid_role_rejected(store):
    _, repo = store
    kb = repo.create_kb("库", workspace_id="ws1", owner_id="u1")
    with pytest.raises(ValueError):
        kb_access.grant(kb_id=kb.id, workspace_id="ws1", user_id="u2", role="admin")
