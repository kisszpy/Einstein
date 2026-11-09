"""分享：token 即权限、过期即失效、撤销幂等。

这里的断言都围绕"对外契约"——分享一旦发出去就不再是自己人，任何一条
判断写松了，泄露的就是别人的资料。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import shares


@pytest.fixture(autouse=True)
def _clean():
    shares._table().delete_where("1=1")
    yield
    shares._table().delete_where("1=1")


def _make(**kw) -> dict:
    base = dict(
        workspace_id="ws1", owner_id="u1", kind="conversation", target_id="conv1",
        title="死锁排查记录",
    )
    base.update(kw)
    return shares.create_share(**base)


def test_token_is_random_and_url_safe():
    a, b = _make(), _make(target_id="conv2")
    assert a["id"] != b["id"]
    assert len(a["id"]) >= 12
    assert all(ch.isalnum() or ch in "-_" for ch in a["id"])


def test_created_share_is_live_and_readable_by_token():
    share = _make()
    got = shares.get_share(share["id"])
    assert got is not None
    assert shares.is_live(got)


def test_unknown_token_is_not_live():
    assert shares.get_share("nope") is None
    assert shares.is_live(None) is False


def test_revoked_share_goes_dead():
    share = _make()
    shares.revoke(share["id"], workspace_id="ws1", owner_id="u1")
    assert shares.is_live(shares.get_share(share["id"])) is False


def test_only_owner_can_revoke():
    share = _make()
    # 同空间同事、甚至猜对 token，也不能撤销别人的分享
    assert shares.revoke(share["id"], workspace_id="ws1", owner_id="u2") is None
    assert shares.is_live(shares.get_share(share["id"])) is True


def test_expired_share_is_dead_even_before_cleanup():
    share = _make()
    rows = shares._all()
    rows[0]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat(timespec="seconds")
    shares._save_all(rows)
    assert shares.is_live(shares.get_share(share["id"])) is False


def test_expires_days_sets_future_deadline():
    share = _make(expires_days=3)
    assert share["expires_at"]
    assert shares.is_live(share) is True


def test_bad_expiry_string_is_treated_as_expired():
    row = {"expires_at": "不是时间"}
    assert shares.is_live({**row, "revoked": False}) is False


def test_conversation_share_cannot_allow_ask():
    share = _make(kind="conversation", allow_ask=True)
    assert share["allow_ask"] is False


def test_kb_share_can_allow_ask():
    share = _make(kind="kb", target_id="kb1", allow_ask=True)
    assert share["allow_ask"] is True


def test_rejects_unknown_kind_and_empty_target():
    with pytest.raises(ValueError):
        _make(kind="workspace")
    with pytest.raises(ValueError):
        _make(target_id="  ")


def test_list_only_shows_own_shares_in_same_workspace():
    _make()
    _make(owner_id="u2")
    _make(workspace_id="ws2")
    rows = shares.list_shares("ws1", "u1")
    assert len(rows) == 1


def test_touch_view_counts_and_stamps():
    share = _make()
    shares.touch_view(share["id"])
    shares.touch_view(share["id"])
    got = shares.get_share(share["id"])
    assert got is not None
    assert int(got["view_count"]) == 2
    assert got["last_viewed_at"]


def test_payload_hides_internal_fields():
    data = shares.payload(_make(note="给评审看"))
    assert "live" in data and data["live"] is True
    assert data["note"] == "给评审看"


# ---------------------------------------------------------------- 路由层
#
# 这一组验的不是服务逻辑，而是"中间件没把免登录入口拦下来"：
# 全站鉴权对 /api/* 一律要求会话，分享链接必须豁免，否则对外就是一个 401。


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_public_read_needs_no_login():
    from app.services.history import get_history_store

    get_history_store().upsert_conversation(
        "conv-share", "死锁是什么", "互相等待。", "死锁排查", "default", "u1",
    )
    share = shares.create_share(
        workspace_id="default", owner_id="u1", kind="conversation",
        target_id="conv-share", title="死锁排查",
    )
    with _client() as client:
        res = client.get(f"/api/public/shares/{share['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "conversation"
    assert body["conversation"]["messages"][0]["content"] == "死锁是什么"
    # 访问一次就记一次，分享者能看出这份链接有没有人真的看过
    assert int(shares.get_share(share["id"])["view_count"]) == 1


def test_revoked_link_404s():
    from app.services.history import get_history_store

    get_history_store().upsert_conversation(
        "conv-share2", "问", "答", "临时", "default", "u1",
    )
    share = shares.create_share(
        workspace_id="default", owner_id="u1", kind="conversation",
        target_id="conv-share2",
    )
    shares.revoke(share["id"], workspace_id="default", owner_id="u1")
    with _client() as client:
        assert client.get(f"/api/public/shares/{share['id']}").status_code == 404


def test_kb_share_without_allow_ask_rejects_questions():
    share = shares.create_share(
        workspace_id="default", owner_id="u1", kind="kb",
        target_id="kb-x", allow_ask=False,
    )
    with _client() as client:
        res = client.post(
            f"/api/public/shares/{share['id']}/ask", json={"question": "能问吗"}
        )
    assert res.status_code == 403
