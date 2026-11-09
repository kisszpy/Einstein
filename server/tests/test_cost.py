"""用量与花费聚合：按天 / 模型 / 成员 / 入口四个视角。

重点验两件事：
1. token 真的落到了 run 行上（`note_tokens` 之前没有任何调用点，报表永远是 0）；
2. 单价没填的模型标记为 `priced=False`，而不是假装花了 0 元——「本地免费」
   和「线上 40 块」在 0 元里长得一模一样，那是报表误导人。
"""

from __future__ import annotations

import pytest

from app.services import models, traces


@pytest.fixture(autouse=True)
def _isolate():
    from app.db.engine import get_db
    from app.db.schema import migrate

    migrate(get_db())
    traces._table().delete_where("1=1")
    models._table().delete_where("1=1")
    models._invalidate()
    yield
    traces._table().delete_where("1=1")
    models._table().delete_where("1=1")
    models._invalidate()


def _run(model: str, tokens_in: int, tokens_out: int, *, uid: str = "u1", kind: str = "chat"):
    run_id = traces.start(kind=kind, workspace_id="ws1", owner_id=uid, model=model)
    traces.note_tokens(tokens_in, tokens_out)
    return traces.finish(run_id=run_id)


def test_tokens_reach_the_run_row():
    row = _run("m-a", 1000, 500)
    assert row is not None
    stored = traces.get_run(row["run_id"])
    assert stored["tokens_in"] == 1000
    assert stored["tokens_out"] == 500
    assert stored["llm_calls"] == 1


def test_buckets_by_model_and_member():
    _run("m-a", 1000, 0, uid="u1")
    _run("m-a", 500, 0, uid="u2")
    _run("m-b", 300, 0, uid="u1")

    data = traces.usage_summary(workspace_id="ws1", days=7)
    assert data["totals"]["runs"] == 3
    assert data["totals"]["tokens_in"] == 1800

    by_model = {b["model"]: b for b in data["by_model"]}
    assert by_model["m-a"]["runs"] == 2
    assert by_model["m-a"]["tokens_in"] == 1500
    assert by_model["m-b"]["tokens_in"] == 300

    by_member = {b["owner_id"]: b for b in data["by_member"]}
    assert by_member["u1"]["tokens_in"] == 1300
    assert by_member["u2"]["tokens_in"] == 500

    assert data["by_kind"][0]["kind"] == "chat"
    assert len(data["by_day"]) >= 1


def test_cost_uses_configured_price():
    models.create_model(
        name="线上模型", base_url="https://api.example.com/v1", model="m-price",
        workspace_id="ws1", owner_id="u1", price_in=2.0, price_out=8.0,
    )
    _run("m-price", 1_000_000, 500_000)

    data = traces.usage_summary(workspace_id="ws1", days=7)
    # 1M 输入 × 2 元 + 0.5M 输出 × 8 元 = 6 元
    assert data["totals"]["cost"] == pytest.approx(6.0, abs=1e-4)
    assert data["by_model"][0]["priced"] is True


def test_unpriced_model_is_flagged_not_zeroed():
    _run("m-free", 1_000_000, 1_000_000)
    data = traces.usage_summary(workspace_id="ws1", days=7)
    row = data["by_model"][0]
    assert row["cost"] == 0
    assert row["priced"] is False
    # 用量照样要能看到
    assert row["tokens_in"] == 1_000_000


def test_other_workspace_excluded():
    _run("m-a", 100, 0)
    traces.start(kind="chat", workspace_id="ws2", owner_id="u9", model="m-a")
    traces.finish(run_id=traces.current_run())

    data = traces.usage_summary(workspace_id="ws2", days=7)
    assert data["totals"]["runs"] == 1
    assert data["totals"]["tokens_in"] == 0


def test_empty_window_shape():
    data = traces.usage_summary(workspace_id="ws1", days=1)
    assert data["totals"]["runs"] == 0
    assert data["by_day"] == []
    assert data["by_model"] == []
