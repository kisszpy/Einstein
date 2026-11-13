"""邮箱验证码：签发、校验、限流。

存内存而不是落库：验证码活 10 分钟，重启丢失的代价只是「重新发一次」，
换取的是「不用为了一个临时凭据再建一张表」。多进程部署时按邮箱哈希做一致性
路由即可；单进程（当前形态）天然一致。

几条硬约束：
- 只存摘要不存明文：日志与内存快照泄露都拿不到可用的码；
- 一次性：校验通过立刻删除；
- 限流按邮箱：60 秒重发、每小时 5 次、5 次错误即作废，防撞库与邮件轰炸。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class OtpError(Exception):
    """带错误码的可预期失败，路由层直接映射成 4xx。"""

    def __init__(self, code: str, message: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass
class _Entry:
    digest: str
    expires_at: float
    attempts: int = 0
    last_sent: float = 0.0
    sent: int = 0
    issued_at: float = field(default_factory=time.monotonic)


_codes: dict[str, _Entry] = {}
_lock = threading.Lock()


def _digest(email: str, code: str) -> str:
    secret = get_settings().secret_key.encode("utf-8")
    return hmac.new(secret, f"{email}|{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def issue(email: str) -> tuple[str, int]:
    """签发一枚验证码，返回 (code, ttl_seconds)。"""
    settings = get_settings()
    key = email.strip().lower()
    now = time.monotonic()
    with _lock:
        entry = _codes.get(key)
        if entry is not None:
            wait = int(entry.last_sent + settings.otp_resend_seconds - now)
            if wait > 0:
                raise OtpError("otp_too_frequent", f"请求过于频繁，请 {wait} 秒后再试", wait)
            if entry.sent >= settings.otp_hourly_limit and now - entry.issued_at < 3600:
                raise OtpError("otp_hourly_limit", "该邮箱今日获取验证码次数过多，请稍后再试")
            sent = entry.sent + 1
            issued_at = entry.issued_at if now - entry.issued_at < 3600 else now
        else:
            sent, issued_at = 1, now

        code = f"{secrets.randbelow(900000) + 100000}"  # 六位，首位非 0
        _codes[key] = _Entry(
            digest=_digest(key, code),
            expires_at=now + settings.otp_ttl_seconds,
            last_sent=now,
            sent=sent,
            issued_at=issued_at,
        )
        # 顺手清掉过期条目，避免长期运行时字典只涨不消
        for stale in [k for k, v in _codes.items() if v.expires_at < now - 300]:
            _codes.pop(stale, None)
    return code, settings.otp_ttl_seconds


def verify(email: str, code: str) -> None:
    """校验验证码。失败抛 OtpError，成功即作废。"""
    settings = get_settings()
    key = email.strip().lower()
    candidate = (code or "").strip()
    if not candidate.isdigit() or len(candidate) != 6:
        raise OtpError("otp_invalid", "验证码不正确")
    now = time.monotonic()
    with _lock:
        entry = _codes.get(key)
        if entry is None:
            raise OtpError("otp_missing", "验证码已失效，请重新获取")
        if entry.expires_at < now:
            _codes.pop(key, None)
            raise OtpError("otp_expired", "验证码已过期，请重新获取")
        if entry.attempts >= settings.otp_max_attempts:
            _codes.pop(key, None)
            raise OtpError("otp_locked", "验证码错误次数过多，请重新获取")
        if not hmac.compare_digest(entry.digest, _digest(key, candidate)):
            entry.attempts += 1
            raise OtpError("otp_invalid", "验证码不正确")
        _codes.pop(key, None)


def clear(email: str) -> None:
    """测试与登出清理用。"""
    with _lock:
        _codes.pop((email or "").strip().lower(), None)


__all__ = ["issue", "verify", "clear", "OtpError"]
