"""v0.7.0 安全与数据基线：限流、配额、级联删除、凭证加密。

这几条此前都是"代码在、实际不生效"的状态，所以每个用例都断言
**行为**而不是"函数存在"：限流必须真的返回 429，配额必须真的累加，
删库必须真的把版本快照清掉。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import mcp, ratelimit


class _Settings:
    """只带被测分支用到的字段，避免拉起完整 Settings。"""

    ratelimit_enabled = True
    ratelimit_login = 3
    ratelimit_chat = 2
    ratelimit_agent = 2
    ratelimit_search = 2
    ratelimit_openapi = 2
    quota_daily_requests = 2
    quota_daily_tokens = 1000
    secret_key = "unit-test-secret"


@pytest.fixture()
def settings(monkeypatch):
    s = _Settings()
    monkeypatch.setattr("app.main.get_settings", lambda: s)
    monkeypatch.setattr("app.services.ratelimit.get_settings", lambda: s)
    monkeypatch.setattr("app.services.mcp.get_settings", lambda: s)
    # 本机可能真的有 Redis 在跑；限流/配额的计数必须落在内存里才可断言
    monkeypatch.setattr(ratelimit, "_redis", None)
    monkeypatch.setattr(ratelimit, "_redis_tried", True)
    ratelimit._memory.clear()
    yield s
    ratelimit._memory.clear()


def _request(user_id: str = "u1"):
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=user_id)))


def test_user_ratelimit_fires_after_identity_is_known(settings):
    from app.main import _rate_limit_user

    req = _request()
    assert _rate_limit_user(req, "/api/chat") is None
    assert _rate_limit_user(req, "/api/chat") is None
    blocked = _rate_limit_user(req, "/api/chat")
    assert blocked is not None and blocked.status_code == 429
    # 不同用户互不影响
    assert _rate_limit_user(_request("u2"), "/api/chat") is None


def test_user_ratelimit_skips_when_no_identity(settings):
    """鉴权之前的请求不该被限流器拦（它们会被 401），也不能因此放行已登录用户。"""
    from app.main import _rate_limit_user

    anon = SimpleNamespace(state=SimpleNamespace())
    assert _rate_limit_user(anon, "/api/chat") is None


def test_request_quota_accumulates(settings):
    """只 check 不 incr 曾导致日请求配额是空转的假开关。"""
    ws = "ws-quota"
    assert ratelimit.quota_check(ws, "requests") == (True, 0)
    ratelimit.quota_incr(ws, "requests")
    assert ratelimit.quota_check(ws, "requests") == (True, 1)
    ratelimit.quota_incr(ws, "requests")
    allowed, used = ratelimit.quota_check(ws, "requests")
    assert allowed is False and used == 2


def test_delete_kb_cascades_document_versions(tmp_path):
    from app.services.repositories.kb import JsonKbRepository

    repo = JsonKbRepository(tmp_path / "kb.json")
    kb = repo.create_kb("合同库", workspace_id="ws", owner_id="u1")
    doc = repo.add_document(kb.id, "a.pdf", "正文", 1, content_hash="h1")
    repo.add_version(
        document_id=doc.id, kb_id=kb.id, version=1, content="正文",
        content_hash="h1", chunk_count=1,
    )
    assert repo.list_versions(doc.id)

    repo.delete_kb(kb.id)
    assert repo.list_versions(doc.id) == []


def test_delete_document_cascades_document_versions(tmp_path):
    from app.services.repositories.kb import JsonKbRepository

    repo = JsonKbRepository(tmp_path / "kb.json")
    kb = repo.create_kb("制度库", workspace_id="ws", owner_id="u1")
    doc = repo.add_document(kb.id, "b.pdf", "正文", 1, content_hash="h2")
    repo.add_version(
        document_id=doc.id, kb_id=kb.id, version=1, content="正文",
        content_hash="h2", chunk_count=1,
    )
    repo.delete_document(doc.id)
    assert repo.list_versions(doc.id) == []


def test_mcp_credentials_roundtrip_and_tamper_detection(settings):
    plain = "sk-live-abcdef123456"
    enc = mcp.encrypt_value(plain)
    assert enc.startswith("enc:v2:")
    assert mcp.decrypt_value(enc) == plain

    # 改一个字符必须解不出来（v1 时代这里是静默返回乱码）
    tampered = enc[:-4] + ("AAAA" if not enc.endswith("AAAA") else "BBBB")
    assert mcp.decrypt_value(tampered) == ""


def test_mcp_v1_ciphertext_still_decrypts(settings):
    """存量 v1 密文不能因为升级而全废。"""
    import base64
    import hashlib

    key = hashlib.sha256(("mcp-env:" + settings.secret_key).encode()).digest()
    iv = b"\x00" * 16
    plain = b"legacy-token"
    ct = mcp._keystream_xor(key, iv, plain)
    v1 = "enc:v1:" + base64.urlsafe_b64encode(iv + ct).decode()
    assert mcp.decrypt_value(v1) == plain.decode()
