"""告警：触发条件、冷却、投递留痕。

告警的失败方式有两种，两种都要防：
- **不报**（阈值/口径错、定时器没跑）——用户以为有人看着；
- **乱报**（冷却失效、阈值 0 也报）——所有人把这个群静音，等于不报。
"""

from __future__ import annotations

import pytest

from app.services import alerts, models, traces


@pytest.fixture(autouse=True)
def _isolate():
    from app.db.engine import get_db
    from app.db.schema import migrate

    migrate(get_db())
    for name in ("alert_rules", "alert_events"):
        alerts._table(name).delete_where("1=1")
    traces._table().delete_where("1=1")
    models._table().delete_where("1=1")
    models._invalidate()
    yield
    for name in ("alert_rules", "alert_events"):
        alerts._table(name).delete_where("1=1")
    traces._table().delete_where("1=1")
    models._table().delete_where("1=1")


def _run(status: str = "ok", model: str = "m") -> None:
    run_id = traces.start(kind="chat", workspace_id="ws1", owner_id="u1", model=model)
    traces.note_tokens(100, 50)
    traces.finish(status=status, error_kind="boom" if status != "ok" else "", run_id=run_id)


def _rule(**kw) -> dict:
    base = dict(
        workspace_id="ws1", owner_id="u1", name="错误率过高", kind="error_rate",
        threshold=0.2, channels=[{"type": "webhook", "url": "https://hook.example.com/x"}],
    )
    base.update(kw)
    return alerts.create_rule(**base)


async def test_fires_when_threshold_breached(monkeypatch):
    _run("ok")
    _run("error")
    _rule()

    sent: list[str] = []

    async def _fake_deliver(channels, text):  # noqa: ANN001
        sent.append(text)
        return True, ""

    monkeypatch.setattr(alerts, "deliver", _fake_deliver)

    result = await alerts.evaluate("ws1")
    assert result["checked"] == 1
    assert len(result["fired"]) == 1
    assert "错误率" in sent[0]
    assert result["fired"][0]["delivered"] is True

    events = alerts.list_events("ws1")
    assert len(events) == 1
    assert events[0]["value"] == pytest.approx(0.5)
    # 触发时间回写到规则，下一次冷却才有依据
    assert alerts.get_rule(result["fired"][0]["rule_id"])["last_fired_at"]


async def test_no_fire_below_threshold(monkeypatch):
    _run("ok")
    _run("ok")
    _run("error")   # 1/3 ≈ 0.33 > 0.2 会触发，所以阈值抬到 0.9
    alerts.update_rule(_rule()["id"], {"threshold": 0.9})
    monkeypatch.setattr(alerts, "deliver", lambda c, t: _ok())
    result = await alerts.evaluate("ws1")
    assert result["fired"] == []


async def _ok():
    return True, ""


async def test_zero_threshold_never_fires(monkeypatch):
    _run("error")
    _rule(threshold=0)
    monkeypatch.setattr(alerts, "deliver", lambda c, t: _ok())
    result = await alerts.evaluate("ws1")
    assert result["fired"] == []


async def test_cooldown_blocks_repeat(monkeypatch):
    _run("error")
    rule = _rule(cooldown_minutes=60)

    async def _deliver(channels, text):  # noqa: ANN001
        return True, ""

    monkeypatch.setattr(alerts, "deliver", _deliver)

    first = await alerts.evaluate("ws1")
    assert len(first["fired"]) == 1
    second = await alerts.evaluate("ws1")
    assert second["fired"] == []          # 一小时内不重复报
    assert len(alerts.list_events("ws1")) == 1

    # 冷却改成 0 之后立刻可以再报
    alerts.update_rule(rule["id"], {"cooldown_minutes": 0})
    third = await alerts.evaluate("ws1")
    assert len(third["fired"]) == 1


async def test_delivery_failure_is_recorded(monkeypatch):
    _run("error")
    _rule()

    async def _fail(channels, text):  # noqa: ANN001
        return False, "HTTP 404"

    monkeypatch.setattr(alerts, "deliver", _fail)
    await alerts.evaluate("ws1")
    event = alerts.list_events("ws1")[0]
    assert event["delivered"] is False
    assert "404" in event["delivery_error"]


async def test_model_down_counts_failed_models(monkeypatch):
    healthy = models.create_model(
        name="正常", base_url="https://a.example.com/v1", model="m1",
        workspace_id="ws1", owner_id="u1",
    )
    broken = models.create_model(
        name="挂了", base_url="https://b.example.com/v1", model="m2",
        workspace_id="ws1", owner_id="u1",
    )
    models.record_probe(healthy["id"], ok=True)
    models.record_probe(broken["id"], ok=False, error="ConnectTimeout")

    value, detail = alerts.measure("model_down", "ws1", 24)
    assert value == 1
    assert "挂了" in detail

    alerts.create_rule(
        workspace_id="ws1", owner_id="u1", name="模型不可用", kind="model_down",
        threshold=1, channels=[{"url": "https://hook.example.com/x"}],
    )
    monkeypatch.setattr(alerts, "deliver", lambda c, t: _ok())
    result = await alerts.evaluate("ws1")
    assert len(result["fired"]) == 1
    assert result["fired"][0]["kind"] == "model_down"


async def test_disabled_rule_skipped(monkeypatch):
    _run("error")
    _rule(enabled=False)
    monkeypatch.setattr(alerts, "deliver", lambda c, t: _ok())
    result = await alerts.evaluate("ws1")
    assert result["checked"] == 0
    assert result["fired"] == []


def test_rule_validation():
    with pytest.raises(ValueError):
        alerts.create_rule(
            workspace_id="ws1", owner_id="u1", name="x", kind="nope", threshold=1
        )
    with pytest.raises(ValueError):
        alerts.create_rule(
            workspace_id="ws1", owner_id="u1", name="  ", kind="error_rate", threshold=1
        )


def test_channels_without_url_are_dropped():
    rule = alerts.create_rule(
        workspace_id="ws1", owner_id="u1", name="x", kind="error_rate", threshold=1,
        channels=[{"type": "webhook", "url": ""}, {"url": "https://hook.example.com/a"}],
    )
    assert len(rule["channels"]) == 1


def test_payload_labels():
    rule = _rule()
    payload = alerts.rule_payload(rule)
    assert payload["kind_label"] == "错误率"
    assert payload["threshold"] == 0.2
    assert payload["enabled"] is True
