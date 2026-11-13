"""预览票据：给 iframe / img 这类带不了 Authorization 头的请求用。

浏览器的 `<iframe src>`、`<img src>` 不会走 fetch 拦截器，控制台的
Bearer 令牌进不去。这里签发短期、限范围的票据，用查询参数或路径段带上：

- 只对 GET 生效；
- 只放行 `/api/projects/`、`/api/browse/` 与 `/api/ppt/thumbs/` 三个前缀
  （产物预览、网页代理、结果卡缩略图）；
- 15 分钟过期。

**令牌是自证的（HMAC 签名），不落任何存储。** 早先是进程内 dict，带来两个
真问题：

1. 后端一重启，所有票据作废，但前端把票据缓存在模块变量里（有效期减 60s），
   于是 iframe 拿着旧票请求 → 面板里直接显示
   `{"detail":"未登录或登录已过期，请重新登录"}` 这串 JSON；
2. 多副本部署时，A 进程签的票 B 进程不认，请求落在别的副本上就是随机 401。

现在令牌自带 user_id / 空间 / 过期时间，只验签不查表：重启、多副本都有效，
安全性质不变（同样的 15 分钟、只 GET、只白名单前缀）。签名密钥取
`APP_SECRET_KEY`——生产必须覆盖默认值，否则等于没有签名（启动日志已有告警）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from app.core.config import get_settings

TTL_SEC = 900
ALLOWED_PREFIXES = ("/api/projects/", "/api/browse/", "/api/ppt/thumbs/")

# 产物的子资源（样式/图片/站内跳转）走 /file/{ticket}/... 路径段带票据：
# iframe 内的相对路径无法附加查询参数，只能把票据编进目录。
# 令牌由「base64url 载荷 + 定长签名」拼成，整串只含 [A-Za-z0-9_-]，才能整段塞进路径。
PATH_TICKET = re.compile(r"^/api/projects/[^/]+/file/([A-Za-z0-9_-]{16,})/")

SIG_LEN = 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    secret = get_settings().secret_key.encode()
    digest = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
    return _b64(digest)[:SIG_LEN]


def issue(user_id: str, workspace_id: str, ttl: int = TTL_SEC) -> dict:
    # n 是随机数，不是防御措施：载荷里必须有一份每次不同的内容，否则同一秒内
    # 为同一用户签出的两串令牌完全一致。缩略图 URL 拿票据当查询参数，
    # 令牌不变就等于 URL 不变，浏览器与中间缓存会一直吐旧图。
    payload = _b64(
        json.dumps(
            {
                "u": user_id,
                "w": workspace_id,
                "e": int(time.time()) + ttl,
                "n": secrets.token_hex(4),
            },
            separators=(",", ":"),
        ).encode()
    )
    return {"ticket": payload + _sign(payload), "expires_in": ttl}


def resolve(token: str | None, path: str, method: str = "GET") -> dict | None:
    """验票。任何一处不满足（作用域/签名/有效期）都返回 None，调用方按未登录处理。"""
    if not token:
        return None
    if method != "GET":
        return None
    if not path.startswith(ALLOWED_PREFIXES):
        return None
    if len(token) <= SIG_LEN:
        return None
    payload, signature = token[:-SIG_LEN], token[-SIG_LEN:]
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        data = json.loads(_unb64(payload))
        expires = int(data["e"])
        user_id = str(data["u"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires < time.time():
        return None
    return {"user_id": user_id, "workspace": str(data.get("w", "")), "expires": expires}


def token_from(path: str, query_ticket: str | None) -> str | None:
    """查询参数优先，其次路径段里的票据（产物静态资源用后者）。"""
    if query_ticket:
        return query_ticket
    matched = PATH_TICKET.match(path)
    return matched.group(1) if matched else None
