"""人工确认记录：归属、时效、参数指纹、一次性。

这四条每一条都对应一个真实的安全/体验事故，所以分开断言——合并成一个
"审批能用"的大测试，改坏其中一条也照样绿。
"""

from __future__ import annotations

import uuid

from app.services import approvals

WS = "ws-approval-test"
UID = "u-owner"
OTHER = "u-stranger"


def _session() -> str:
    """每个用例一个独立 session：审批表是全局的，隔离靠会话 id。"""
    return f"s-{uuid.uuid4().hex[:8]}"


def test_request_then_approve():
    session = _session()
    record = approvals.request(WS, UID, session, "code_run", args_hash="abc")
    assert record["decision"] == "pending"
    # 只是「等待确认」，还不能当批准用
    assert approvals.latest_decision(WS, UID, session) is None
    assert approvals.granted_tools(WS, UID, session) == set()

    decided = approvals.decide(WS, UID, session, decision="approve", args_hash="abc")
    assert decided is not None
    assert decided["decision"] == "approve"
    assert approvals.latest_decision(WS, UID, session)["decision"] == "approve"


def test_args_fingerprint_must_match():
    """参数变了就不是同一个调用：不能沿用上一条批准。"""
    session = _session()
    approvals.request(WS, UID, session, "code_run", args_hash="hash-a")
    assert approvals.decide(WS, UID, session, args_hash="hash-b") is None
    assert approvals.decide(WS, UID, session, args_hash="hash-a") is not None


def test_other_owner_cannot_decide():
    """归属：拿到别人的 session_id 也批不了（接口层统一 404）。"""
    session = _session()
    approvals.request(WS, UID, session, "code_run", args_hash="x")
    assert approvals.decide(WS, OTHER, session, args_hash="x") is None
    assert approvals.latest_decision(WS, OTHER, session) is None


def test_other_workspace_cannot_decide():
    session = _session()
    approvals.request(WS, UID, session, "code_run", args_hash="x")
    assert approvals.decide("ws-other", UID, session, args_hash="x") is None


def test_consumed_is_one_shot():
    """批准只够续跑一次：消费之后既不能再续跑，也不再是"已批准工具"。"""
    session = _session()
    record = approvals.request(WS, UID, session, "code_run", args_hash="x")
    approvals.decide(WS, UID, session, args_hash="x")
    assert approvals.granted_tools(WS, UID, session) == {"code_run"}

    approvals.consume(record["id"])
    assert approvals.latest_decision(WS, UID, session) is None
    assert approvals.granted_tools(WS, UID, session) == set()


def test_expired_is_not_a_decision():
    """过期即视为未批准：宁可多问一次，不可永久放行。"""
    session = _session()
    record = approvals.request(WS, UID, session, "code_run", args_hash="x")
    approvals._db().execute(
        "UPDATE agent_approvals SET expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", record["id"]),
    )
    assert approvals.decide(WS, UID, session, args_hash="x") is None
    assert approvals.latest_decision(WS, UID, session) is None


def test_purge_expired_removes_rows():
    session = _session()
    record = approvals.request(WS, UID, session, "code_run", args_hash="x")
    approvals._db().execute(
        "UPDATE agent_approvals SET expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", record["id"]),
    )
    assert approvals.purge_expired() >= 1
    assert approvals._db().query(
        "SELECT id FROM agent_approvals WHERE id = ?", (record["id"],)
    ) == []


def test_args_fingerprint_is_stable_and_order_insensitive():
    assert approvals.args_fingerprint({"code": "print(1)", "lang": "py"}) == \
        approvals.args_fingerprint({"lang": "py", "code": "print(1)"})
    assert approvals.args_fingerprint({"code": "print(1)"}) != \
        approvals.args_fingerprint({"code": "print(2)"})


# ---------------------------------------------------------------- 会话级授权
#
# 「本次会话都允许」比单次批准多两个必须守住的性质：它要能持续生效（不被
# 消费掉），又不能污染单次决定的查询（否则续跑会拿到一条用不完的记录）。


def test_session_grant_persists_and_survives_consume():
    session = _session()
    approvals.request(WS, UID, session, "code_run", args_hash="h1")
    decided = approvals.decide(WS, UID, session, decision="approve",
                               args_hash="h1", scope="session")
    assert decided is not None
    assert approvals.session_granted(WS, UID, session, "code_run") is True

    # 续跑会消费单次批准；会话授权不能被一起消费掉，否则第二个调用又要问
    approvals.consume(decided["id"])
    assert approvals.session_granted(WS, UID, session, "code_run") is True


def test_session_grant_is_not_a_one_shot_decision():
    """latest_decision 只认单次决定：会话授权混进来会让拒绝被静默覆盖。"""
    session = _session()
    approvals.request(WS, UID, session, "code_run", args_hash="h2")
    once = approvals.decide(WS, UID, session, decision="approve",
                            args_hash="h2", scope="session")
    assert once is not None
    # 消费掉这一次的决定之后，续跑不该再取到任何东西——
    # 如果 session 记录混进了 latest_decision，这里会返回它而不是 None
    approvals.consume(once["id"])
    assert approvals.latest_decision(WS, UID, session) is None


def test_session_grant_scoped_to_tool_and_session():
    session = _session()
    approvals.request(WS, UID, session, "code_run", args_hash="h3")
    approvals.decide(WS, UID, session, decision="approve", args_hash="h3", scope="session")
    assert approvals.session_granted(WS, UID, session, "code_run") is True
    # 别的工具、别的会话、别的用户都不沾光
    assert approvals.session_granted(WS, UID, session, "mcp_tool") is False
    assert approvals.session_granted(WS, UID, _session(), "code_run") is False
    assert approvals.session_granted(WS, OTHER, session, "code_run") is False


def test_session_grant_replaces_not_accumulates():
    """重复授权只刷新，不叠加：否则过期判断要在一堆记录里挑最新的。"""
    session = _session()
    for _ in range(3):
        approvals.request(WS, UID, session, "code_run", args_hash="h4")
        approvals.decide(WS, UID, session, decision="approve", args_hash="h4", scope="session")
    rows = approvals._db().query(
        "SELECT id FROM agent_approvals WHERE workspace_id = ? AND owner_id = ? "
        "AND session_id = ? AND scope = 'session'",
        (WS, UID, session),
    )
    assert len(rows) == 1
