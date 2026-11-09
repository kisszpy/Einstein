"""运行轨迹：能写、能读、能按工作区隔离、能算出健康度。"""

from __future__ import annotations

import uuid

from app.services import traces


def _ws() -> str:
    return f"ws-{uuid.uuid4().hex[:8]}"


def test_trace_roundtrip_captures_retrieval_and_tools():
    ws = _ws()
    run_id = traces.start(kind="agent", workspace_id=ws, owner_id="u1", session_id="s1")
    traces.note_retrieval(
        query="合同违约金上限",
        rewritten="合同法 违约金 上限",
        mode="hybrid",
        candidates=2,
        hits=[{"chunk_id": "c1", "doc": "合同.pdf", "score": 0.81}],
        ms=42,
        missed=False,
        rerank="none",
    )
    traces.note_tool("kb_search", 37, "ok")
    traces.note_tokens(tokens_in=100, tokens_out=50)
    row = traces.finish(status="ok")

    assert row is not None and row["run_id"] == run_id
    got = traces.get_run(run_id)
    assert got is not None
    assert got["retrieval"]["query"] == "合同违约金上限"
    assert got["retrieval"]["hits"][0]["chunk_id"] == "c1"
    assert got["tool_calls"][0]["name"] == "kb_search"
    assert got["tokens_in"] == 100 and got["tokens_out"] == 50
    assert got["duration_ms"] >= 0


def test_trace_isolated_by_workspace():
    ws_a, ws_b = _ws(), _ws()
    rid = traces.start(kind="chat", workspace_id=ws_a, owner_id="u1")
    traces.finish(status="ok")
    assert rid in {r["run_id"] for r in traces.list_runs(workspace_id=ws_a)}
    assert rid not in {r["run_id"] for r in traces.list_runs(workspace_id=ws_b)}


def test_summary_reports_miss_rate_and_errors():
    ws = _ws()
    # 一次未命中的检索
    traces.start(kind="chat", workspace_id=ws, owner_id="u1")
    traces.note_retrieval(query="库里没有的问题", candidates=0, hits=[], missed=True)
    traces.finish(status="ok")
    # 一次失败
    traces.start(kind="agent", workspace_id=ws, owner_id="u1")
    traces.finish(status="error", error_kind="TimeoutError")

    s = traces.summary(workspace_id=ws, limit=100)
    assert s["runs"] == 2
    assert s["miss_rate"] == 1.0
    assert s["error_rate"] == 0.5
    assert s["errors"].get("TimeoutError") == 1


def test_finish_is_idempotent():
    ws = _ws()
    traces.start(kind="chat", workspace_id=ws, owner_id="u1")
    assert traces.finish(status="ok") is not None
    assert traces.finish(status="ok") is None
