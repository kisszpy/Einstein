"""统一日志的行为约定。

锁三件事，它们都是「看起来能用、出事后才发现不对」的类型：
1. setup_logging 幂等——create_app 会被测试反复触发，重复加 handler 会导致
   每条日志打两遍（真踩过，日志量翻倍还以为是请求变多了）；
2. 格式里必须带 request_id，否则并发下日志无法归因；
3. 中间件必须把 id 回写到 X-Request-Id，前端报错截图才有对应的服务端记录。
"""

from __future__ import annotations

import logging

from app.core import logging as app_logging
from app.core.logging import _RequestIdFilter, request_id


def test_setup_logging_is_idempotent(monkeypatch) -> None:
    """重复调用只保留一个 root handler。"""
    monkeypatch.setattr(app_logging, "_configured", False)
    root = logging.getLogger()

    app_logging.setup_logging("INFO")
    first = len(root.handlers)
    assert first == 1, "setup_logging 应只挂一个 handler"

    # 二次调用必须是 no-op（_configured 短路），不能把 handler 再叠一层
    app_logging.setup_logging("DEBUG")
    assert len(root.handlers) == first


def test_force_reinstalls_single_handler(monkeypatch) -> None:
    """force=True 重挂 handler：uvicorn 晚到的 configure_logging 会覆盖掉我们那套。

    嵌入式部署（先 import app、再起 Server）就是这个顺序：uvicorn 在应用导入
    之后才装自己的 handler，不重挂一遍就会出现「两套格式混在一个 stdout 里」。
    重挂必须仍然只有一个 handler，否则日志翻倍。
    """
    monkeypatch.setattr(app_logging, "_configured", False)
    root = logging.getLogger()

    app_logging.setup_logging("INFO")
    app_logging.setup_logging("INFO", force=True)

    ours = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(ours) == 1, "重挂后仍应只有一个输出 handler"


def test_filter_stamps_request_id() -> None:
    """Filter 把 contextvar 的值贴到 record 上；非请求上下文取默认 '-'。"""
    flt = _RequestIdFilter()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)

    token = request_id.set("abc123")
    try:
        assert flt.filter(record) is True
        assert record.request_id == "abc123"
    finally:
        request_id.reset(token)

    plain = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
    flt.filter(plain)
    assert plain.request_id == "-"


def test_formatter_renders_request_id_and_level() -> None:
    flt = _RequestIdFilter()
    fmt = logging.Formatter(app_logging._FMT)
    record = logging.LogRecord("app.services.rag", logging.WARNING, __file__, 1, "检索降级", None, None)

    token = request_id.set("deadbeef")
    try:
        flt.filter(record)
        out = fmt.format(record)
    finally:
        request_id.reset(token)

    assert "[deadbeef]" in out
    assert "WARNING" in out
    assert "app.services.rag" in out
    assert "检索降级" in out


def test_middleware_returns_request_id_header() -> None:
    """前端拿到的 X-Request-Id 必须与服务端日志里的一致。

    客户端传了就沿用（浏览器侧报错能直接对上），没传则由服务端生成。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        res = client.get("/api/health", headers={"x-request-id": "trace-me-001"})
        assert res.status_code == 200
        assert res.headers.get("X-Request-Id") == "trace-me-001"

        res2 = client.get("/api/health")
        rid = res2.headers.get("X-Request-Id")
        assert rid and len(rid) >= 8, "未传时应自动生成一个非空 id"
