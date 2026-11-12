"""预览票据：给 iframe / img 这类带不了 Authorization 头的请求用。

浏览器的 `<iframe src>`、`<img src>` 不会走 fetch 拦截器，控制台的
Bearer 令牌进不去。这里签发短期、限范围的票据，用查询参数或路径段带上：

- 只对 GET 生效；
- 只放行 `/api/projects/`、`/api/browse/` 与 `/api/ppt/thumbs/` 三个前缀
  （产物预览、网页代理、结果卡缩略图）；
- 15 分钟过期，进程内存储，重启即失效（预览出问题不会变成长期后门）。
"""

from __future__ import annotations

import re
import secrets
import threading
import time

TTL_SEC = 900
ALLOWED_PREFIXES = ("/api/projects/", "/api/browse/", "/api/ppt/thumbs/")

# 产物的子资源（样式/图片/站内跳转）走 /file/{ticket}/... 路径段带票据：
# iframe 内的相对路径无法附加查询参数，只能把票据编进目录。
PATH_TICKET = re.compile(r"^/api/projects/[^/]+/file/([A-Za-z0-9_-]{16,})/")

_lock = threading.Lock()
_grants: dict[str, dict] = {}


def _sweep(now: float) -> None:
    for key in [k for k, v in _grants.items() if v["expires"] < now]:
        _grants.pop(key, None)


def issue(user_id: str, workspace_id: str, ttl: int = TTL_SEC) -> dict:
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        _sweep(now)
        _grants[token] = {
            "user_id": user_id,
            "workspace": workspace_id,
            "expires": now + ttl,
        }
    return {"ticket": token, "expires_in": ttl}


def resolve(token: str | None, path: str, method: str = "GET") -> dict | None:
    if not token:
        return None
    if method != "GET":
        return None
    if not path.startswith(ALLOWED_PREFIXES):
        return None
    with _lock:
        grant = _grants.get(token)
        if grant is None:
            return None
        if grant["expires"] < time.time():
            _grants.pop(token, None)
            return None
        return dict(grant)


def revoke(token: str) -> None:
    with _lock:
        _grants.pop(token, None)


def token_from(path: str, query_ticket: str | None) -> str | None:
    """查询参数优先，其次路径段里的票据（产物静态资源用后者）。"""
    if query_ticket:
        return query_ticket
    matched = PATH_TICKET.match(path)
    return matched.group(1) if matched else None
