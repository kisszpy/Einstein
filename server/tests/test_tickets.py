"""预览票据：iframe / img 拿不到 Authorization 头，只能靠它。

**这组用例守的是一个已经踩过的坑**：票据早先是进程内 dict，后端一重启就全部
作废，而前端把票据缓存在模块变量里 14 分钟——用户点「预览」，面板里直接渲染
出 `{"detail":"未登录或登录已过期，请重新登录"}` 这串 JSON，看起来像掉登录。
现在令牌自证（HMAC 签名，不查表），重启与多副本都不影响。
"""

from __future__ import annotations

import time

import pytest

from app.services import tickets

PREVIEW = "/api/projects/demo/preview"
FILE = "/api/projects/demo/file"


def test_issue_and_resolve_round_trip() -> None:
    grant = tickets.issue("u1", "ws-1")
    assert grant["expires_in"] == tickets.TTL_SEC
    assert tickets.resolve(grant["ticket"], PREVIEW) == {
        "user_id": "u1",
        "workspace": "ws-1",
        "expires": pytest.approx(grant["expires_in"] + time.time(), abs=2),
    }


def test_ticket_survives_process_restart() -> None:
    """令牌不落存储：模块里不该再有进程内的票据表。

    这条是防回归的核心——谁把 `_grants` 那个 dict 加回来，重启丢票的老毛病
    就跟着回来，而它只会以「点预览突然 401」的形式出现，很难联想。
    """
    assert not hasattr(tickets, "_grants")
    # 同一张票连续验两次都成立，不依赖「先 issue 再 resolve」的进程内顺序
    token = tickets.issue("u1", "ws-1")["ticket"]
    assert tickets.resolve(token, PREVIEW) is not None
    assert tickets.resolve(token, PREVIEW) is not None


def test_expired_ticket_rejected() -> None:
    token = tickets.issue("u1", "ws-1", ttl=-1)["ticket"]
    assert tickets.resolve(token, PREVIEW) is None


def test_tampered_signature_rejected() -> None:
    token = tickets.issue("u1", "ws-1")["ticket"]
    flipped = "A" if token[-1] != "A" else "B"
    assert tickets.resolve(token[:-1] + flipped, PREVIEW) is None


def test_tampered_payload_rejected() -> None:
    """把载荷换成别人的 user_id 但签名不动，必须验不过。"""
    mine = tickets.issue("u1", "ws-1")["ticket"]
    other = tickets.issue("u2", "ws-1")["ticket"]
    payload_of_other = other[: -tickets.SIG_LEN]
    forged = payload_of_other + mine[-tickets.SIG_LEN :]
    assert tickets.resolve(forged, PREVIEW) is None


def test_secret_change_invalidates(monkeypatch) -> None:
    """签名密钥换了，老票自然失效——这正是「防伪造」的来源。"""

    class _Other:
        secret_key = "another-secret"

    token = tickets.issue("u1", "ws-1")["ticket"]
    monkeypatch.setattr(tickets, "get_settings", lambda: _Other())
    assert tickets.resolve(token, PREVIEW) is None


def test_scope_is_limited_to_preview_prefixes() -> None:
    token = tickets.issue("u1", "ws-1")["ticket"]
    assert tickets.resolve(token, "/api/agent/runs") is None
    assert tickets.resolve(token, "/api/projects/demo/archive") is not None
    assert tickets.resolve(token, "/v1/chat") is None


def test_only_get_is_allowed() -> None:
    token = tickets.issue("u1", "ws-1")["ticket"]
    assert tickets.resolve(token, PREVIEW, method="POST") is None
    assert tickets.resolve(token, PREVIEW, method="GET") is not None


def test_empty_token_rejected() -> None:
    assert tickets.resolve("", PREVIEW) is None
    assert tickets.resolve(None, PREVIEW) is None


def test_token_is_path_safe() -> None:
    """产物子资源把票据编进路径段，所以整串必须只含 [A-Za-z0-9_-]。"""
    token = tickets.issue("u1", "ws-1")["ticket"]
    assert token == "".join(ch for ch in token if ch.isalnum() or ch in "-_")
    assert tickets.PATH_TICKET.match(f"/api/projects/demo/file/{token}/site.css")
    assert tickets.token_from(f"/api/projects/demo/file/{token}/site.css", None) == token
    # 查询参数优先
    assert tickets.token_from(FILE, "qs-token") == "qs-token"
    # 路径里没有票据时不能凭空造出一个
    assert tickets.token_from("/api/projects/demo/file/site.css", None) is None


# ---------------------------------------------------------------- 中间件层

def test_preview_401_renders_readable_page() -> None:
    """iframe 里不能出现裸 JSON：浏览器会把响应体原样画出来。

    之前用户点「预览」看到的就是 `{"detail":"未登录或登录已过期，请重新登录"}`，
    以为掉了登录。现在这类请求（无 Authorization 头 + 预览前缀）回一张能读懂、
    能自救的页面。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.get(PREVIEW, params={"path": "index.html", "ticket": "broken"})
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("text/html")
    assert "重新打开预览" in res.text
    assert '"detail"' not in res.text


def test_ordinary_api_401_still_json() -> None:
    """普通接口保持 JSON：前端拦截器靠 `detail` 出提示，不能改语义。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.get("/api/projects")
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["detail"] == "未登录或登录已过期，请重新登录"
