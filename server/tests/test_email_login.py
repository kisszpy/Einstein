"""邮箱验证码登录：签发、限流、校验、首次登录即注册。

验证码是「一次性短凭据」，出事的方式很固定：重放、爆破、轰炸。这里把这三种
都钉住，另外确认没配 SMTP 时不会假装发送成功。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services import mailer, otp, users


def _client(loopback: bool = True) -> TestClient:
    """默认模拟本机访问：没配 SMTP 时只有回环地址能拿到验证码。"""
    host = ("127.0.0.1", 50000) if loopback else ("203.0.113.9", 50000)
    return TestClient(app, client=host)


@pytest.fixture(autouse=True)
def _clean_otp():
    otp.clear("new.user@example.com")
    otp.clear("owner@example.com")
    yield
    otp.clear("new.user@example.com")
    otp.clear("owner@example.com")


# ---------------------------------------------------------------- 服务层


def test_code_is_six_digits_and_single_use():
    code, ttl = otp.issue("new.user@example.com")
    assert len(code) == 6 and code.isdigit()
    assert ttl == get_settings().otp_ttl_seconds
    otp.verify("new.user@example.com", code)
    with pytest.raises(otp.OtpError) as exc:
        otp.verify("new.user@example.com", code)
    assert exc.value.code == "otp_missing"


def test_wrong_code_counts_attempts_then_locks():
    code, _ = otp.issue("new.user@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(get_settings().otp_max_attempts):
        with pytest.raises(otp.OtpError):
            otp.verify("new.user@example.com", wrong)
    with pytest.raises(otp.OtpError) as exc:
        otp.verify("new.user@example.com", code)
    assert exc.value.code == "otp_locked"


def test_resend_is_rate_limited():
    otp.issue("new.user@example.com")
    with pytest.raises(otp.OtpError) as exc:
        otp.issue("new.user@example.com")
    assert exc.value.code == "otp_too_frequent"
    assert exc.value.retry_after > 0


def test_code_is_never_stored_in_plain_text():
    code, _ = otp.issue("new.user@example.com")
    entry = otp._codes["new.user@example.com"]
    assert code not in entry.digest
    assert len(entry.digest) == 64


def test_mailer_reports_console_when_smtp_is_missing(caplog):
    assert mailer.configured() is False
    assert mailer.send_code("new.user@example.com", "123456", 600) == "console"


# ---------------------------------------------------------------- 路由层


def test_loopback_can_complete_the_flow_without_smtp(monkeypatch):
    """本机联调：没配 SMTP 也要能走通——验证码在响应里回带，且只在回环地址。"""
    monkeypatch.setattr(mailer, "send_code", lambda to, code, ttl: "console")
    with _client() as client:
        sent = client.post("/api/auth/email/code", json={"email": "new.user@example.com"})
        assert sent.status_code == 200, sent.text
        body = sent.json()
        assert body["delivery"] == "console" and body["code"]

        ok = client.post("/api/auth/email/verify",
                         json={"email": "new.user@example.com", "code": body["code"]})
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["created"] is True
        assert data["token"] and data["user"]["email"] == "new.user@example.com"
        assert data["workspaces"]

        # 同一个邮箱再来一次：不再建号，但仍签发新会话
        again = client.post("/api/auth/email/code", json={"email": "new.user@example.com"})
        second = client.post("/api/auth/email/verify",
                             json={"email": "new.user@example.com", "code": again.json()["code"]})
        assert second.status_code == 200 and second.json()["created"] is False
        assert second.json()["user"]["id"] == data["user"]["id"]


def test_wrong_code_is_rejected_generically(monkeypatch):
    monkeypatch.setattr(mailer, "send_code", lambda to, code, ttl: "smtp")
    with _client(loopback=False) as client:
        sent = client.post("/api/auth/email/code", json={"email": "new.user@example.com"})
        assert sent.status_code == 200 and "code" not in sent.json()
        bad = client.post("/api/auth/email/verify",
                          json={"email": "new.user@example.com", "code": "000000"})
        assert bad.status_code == 401
        assert "验证码" in bad.json()["detail"]


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "", "a b@c.com"])
def test_bad_email_is_rejected(email):
    with _client() as client:
        res = client.post("/api/auth/email/code", json={"email": email})
        assert res.status_code == 400


def test_new_account_cannot_use_password_login(monkeypatch):
    """验证码注册的账号没有可用密码：随机占位，谁都登不进去。"""
    monkeypatch.setattr(mailer, "send_code", lambda to, code, ttl: "console")
    with _client() as client:
        body = client.post("/api/auth/email/code", json={"email": "new.user@example.com"}).json()
        client.post("/api/auth/email/verify",
                    json={"email": "new.user@example.com", "code": body["code"]})
    store = users.get_user_store()
    user = store.by_email("new.user@example.com")
    assert user is not None and user.password_hash
    assert store.authenticate(user.username, "") is None
    assert store.authenticate(user.username, "123456") is None


def test_loopback_falls_back_when_smtp_rejects_the_credentials(monkeypatch):
    """SMTP 配了但账号没权限时，本机不能卡死；生产仍然明确报错。"""

    def reject(to, code, ttl):
        raise mailer.MailError("(550, b'User has no permission')")

    monkeypatch.setattr(mailer, "send_code", reject)
    with _client(loopback=True) as client:
        body = client.post("/api/auth/email/code", json={"email": "new.user@example.com"}).json()
        assert body["delivery"] == "console" and body["code"]
    otp.clear("new.user@example.com")
    with _client(loopback=False) as client:
        res = client.post("/api/auth/email/code", json={"email": "new.user@example.com"})
        assert res.status_code == 502
