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
