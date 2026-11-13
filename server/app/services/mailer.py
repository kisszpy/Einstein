"""发验证码邮件。没配 SMTP 时不假装发出去：明确告诉调用方走的是控制台。

只用标准库 smtplib：为了发一封六位数验证码引入第三方邮件 SDK，会让部署多一个
依赖面，还得跟着升级。
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SUBJECT = "登录验证码"
BODY = """\
你正在登录 {product}，本次验证码：

    {code}

{minutes} 分钟内有效，仅可使用一次。如果不是你本人操作，忽略这封邮件即可。
"""


class MailError(Exception):
    """投递失败。调用方据此提示用户稍后再试，而不是谎称已发送。"""


def configured() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def send_code(to: str, code: str, ttl_seconds: int, product: str = "Einstein") -> str:
    """发送验证码，返回实际通道：`smtp` 或 `console`。

    未配置 SMTP 时只写日志（同时在返回值里说明），绝不静默丢弃——否则用户一直等
    一封永远不会来的邮件。
    """
    settings = get_settings()
    minutes = max(1, ttl_seconds // 60)
    if not configured():
        logger.warning("[mail:console] 未配置 SMTP，验证码 %s 发给 %s（%d 分钟有效）",
                       code, to, minutes)
        return "console"

    message = EmailMessage()
    message["Subject"] = f"{SUBJECT}：{code}"
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = to
    message.set_content(BODY.format(product=product, code=code, minutes=minutes))

    try:
        if settings.smtp_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                  timeout=15, context=ssl.create_default_context()) as smtp:
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("验证码邮件发送失败 %s：%s", to, exc)
        raise MailError(str(exc)) from exc
    return "smtp"


__all__ = ["send_code", "configured", "MailError"]
