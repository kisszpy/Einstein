"""运行事件外置：刷新后被打到别的进程，也要接得回自己那一轮。

这一组的核心断言只有一句：**内存里没有，库里还有，用户就不该看到 404**。
多副本部署下这是「转圈转一半没了」的唯一根因，单进程测试跑不出来。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import run_events, run_registry


@pytest.fixture(autouse=True)
def _clean():
    run_events._state().delete_where("1=1")
    run_registry._RUNS.clear()
    run_registry._TASKS.clear()
    yield
    run_events._state().delete_where("1=1")
    run_registry._RUNS.clear()
    run_registry._TASKS.clear()


def _run(**kw) -> run_registry.RunRecord:
    base = dict(session_id="conv-1", workspace_id="ws-ev", owner_id="u-ev")
    base.update(kw)
    return run_registry.create(**base)


# ---------------------------------------------------------------- 落库


def test_step_event_is_persisted():
    run = _run()
    run.push("step", {"step": {"kind": "tool", "name": "kb_search"}})

    rows = run_events.replay(run.run_id, workspace_id="ws-ev", owner_id="u-ev")
    assert len(rows) == 1
    seq, event, data = rows[0]
    assert event == "step"
    assert seq == 1
    assert data["step"]["name"] == "kb_search"


def test_delta_frames_are_not_persisted():
    """delta 落库会把「流式输出」变成「写数据库」。"""
    run = _run()
    for i in range(200):
        run.push("delta", {"text": f"片{i}"})
    run.push("answer_start", {})

    rows = run_events.replay(run.run_id, workspace_id="ws-ev", owner_id="u-ev")
    assert [e for _, e, _ in rows] == ["answer_start"]
    # 内存里仍然一帧不少
    assert sum(1 for e, _ in run.events if e == "delta") == 200


def test_seq_keeps_growing_after_memory_trim(monkeypatch):
    """内存截断只影响内存；库里的 seq 必须继续单调，否则重放会乱序/重复。"""
    monkeypatch.setattr(run_registry, "MAX_EVENTS", 3)
    run = _run()
    for i in range(8):
        run.push("step", {"i": i})

    assert len(run.events) <= 4  # 软上限：截断发生在下一次 push 之前
    assert run.events[-1] == ("step", {"i": 7})  # 留住的是最新的
    rows = run_events.replay(run.run_id, workspace_id="ws-ev", owner_id="u-ev")
    assert len(rows) == 8
    assert [seq for seq, _, _ in rows] == list(range(1, 9))


def test_persist_failure_degrades_to_memory_only(monkeypatch):
    """落库失败不能打断运行，但必须能看出来「这条运行跨不了副本」。"""
    monkeypatch.setattr(run_events, "append", lambda **_: False)
    run = _run()
    run.push("step", {"i": 0})
    run.push("step", {"i": 1})

    assert run.persisted is False
    assert len(run.events) == 2  # 运行照常
    assert run_registry.stats()["memory_only"] == 1


# ---------------------------------------------------------------- 读与归属


def test_replay_rejects_foreign_owner():
    run = _run()
    run.push("step", {"i": 0})

    assert run_events.replay(run.run_id, workspace_id="ws-ev", owner_id="u-other") == []
    assert run_events.replay(run.run_id, workspace_id="ws-other", owner_id="u-ev") == []
    assert run_registry.has_persisted(run.run_id, workspace_id="ws-ev", owner_id="u-other") is False


def test_replay_after_seq_returns_only_new():
    run = _run()
    run.push("step", {"i": 0})
    run.push("step", {"i": 1})
    run.push("step", {"i": 2})

    fresh = run_events.replay(
        run.run_id, workspace_id="ws-ev", owner_id="u-ev", after_seq=1,
    )
    assert [seq for seq, _, _ in fresh] == [2, 3]


def test_exists_is_false_for_unknown_run():
    assert run_events.exists("never-seen", workspace_id="ws-ev", owner_id="u-ev") is False


# ---------------------------------------------------------------- 未收尾清单


def test_running_skips_finished_runs():
    done = _run()
    done.push("step", {"i": 0})
    done.push("done", {"run_id": done.run_id})

    busy = _run()
    busy.push("step", {"i": 0})

    ids = [r["run_id"] for r in run_events.running("ws-ev", "u-ev")]
    assert ids == [busy.run_id]
    assert ids.count(done.run_id) == 0


def test_active_merges_memory_and_db():
    """本进程新建的那次 + 只有库里有记录的那次，都要出现在「还没看完」里。"""
    local = _run()
    local.push("step", {"i": 0})

    # 模拟「在别的副本上创建」：只往库里写，不登记内存
    from app.services import run_events as ev

    ev.append(
        run_id="remote-run", seq=1, event="step", data={"i": 0},
        workspace_id="ws-ev", owner_id="u-ev", session_id="conv-9",
    )

    rows = run_registry.active("ws-ev", "u-ev")
    by_id = {r["run_id"]: r for r in rows}
    assert by_id[local.run_id]["local"] is True
    assert by_id["remote-run"]["local"] is False
    assert by_id["remote-run"]["session_id"] == "conv-9"


# ---------------------------------------------------------------- 跨进程跟读


async def test_follow_persisted_replays_then_stops_at_terminal():
    from app.services import run_events as ev

    ev.append(run_id="r1", seq=1, event="answer_start", data={},
              workspace_id="ws-ev", owner_id="u-ev")
    ev.append(run_id="r1", seq=2, event="step", data={"i": 0},
              workspace_id="ws-ev", owner_id="u-ev")
    ev.append(run_id="r1", seq=3, event="done", data={"run_id": "r1"},
              workspace_id="ws-ev", owner_id="u-ev")
    ev.append(run_id="r1", seq=4, event="step", data={"i": 99},
              workspace_id="ws-ev", owner_id="u-ev")

    got = [
        e async for e, _ in run_registry.follow_persisted(
            "r1", workspace_id="ws-ev", owner_id="u-ev", poll=0.01,
        )
    ]
    # 读到 done 就收手，done 之后的事件不再发（运行已经结束了）
    assert got == ["answer_start", "step", "done"]


async def test_follow_persisted_picks_up_events_written_later():
    """真跨进程：读者先挂上，事件后到。"""
    from app.services import run_events as ev

    ev.append(run_id="r2", seq=1, event="step", data={"i": 0},
              workspace_id="ws-ev", owner_id="u-ev")

    seen: list[str] = []

    async def reader() -> None:
        async for event, _ in run_registry.follow_persisted(
            "r2", workspace_id="ws-ev", owner_id="u-ev", poll=0.01, idle_seconds=5,
        ):
            seen.append(event)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.05)
    ev.append(run_id="r2", seq=2, event="done", data={},
              workspace_id="ws-ev", owner_id="u-ev")
    await asyncio.wait_for(task, timeout=2.0)

    assert seen == ["step", "done"]


async def test_follow_persisted_gives_up_when_stream_goes_quiet():
    """终态事件没写成功时，宁可断开也不要让连接永远挂着。"""
    from app.services import run_events as ev

    ev.append(run_id="r3", seq=1, event="step", data={},
              workspace_id="ws-ev", owner_id="u-ev")

    got = [
        e async for e, _ in run_registry.follow_persisted(
            "r3", workspace_id="ws-ev", owner_id="u-ev", poll=0.01, idle_seconds=0.1,
        )
    ]
    assert got == ["step"]


# ---------------------------------------------------------------- 路由层


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def auth_header():
    """造一个真会话：中间件在路由依赖之前就鉴权，覆盖依赖绕不过去。"""
    from app.services import sessions, users

    store = users.get_user_store()
    admin = store.list_all()[0]
    token, _ = sessions.get_session_store().issue(admin.id)
    return {"Authorization": f"Bearer {token}"}, admin.id


def test_reconnect_replays_from_db_when_not_in_memory(auth_header):
    headers, uid = auth_header
    from app.services import run_events as ev

    ws = "default"
    for seq, event in enumerate(["step", "done"], start=1):
        ev.append(run_id="ghost-run", seq=seq, event=event, data={"i": seq},
                  workspace_id=ws, owner_id=uid, session_id="conv-x")

    # 内存里没有这次运行——正是「刷新后被打到别的副本」的样子
    assert run_registry._RUNS.get("ghost-run") is None

    with _client() as client:
        res = client.get("/api/agent/runs/ghost-run/stream", headers=headers)

    assert res.status_code == 200
    body = res.text
    assert "event: step" in body
    assert "event: done" in body


def test_reconnect_404_when_nowhere(auth_header):
    headers, _uid = auth_header
    with _client() as client:
        res = client.get("/api/agent/runs/nobody/stream", headers=headers)
    assert res.status_code == 404


def test_active_runs_include_other_replica(auth_header):
    headers, uid = auth_header
    from app.services import run_events as ev

    ev.append(run_id="remote-1", seq=1, event="step", data={},
              workspace_id="default", owner_id=uid, session_id="conv-r")

    with _client() as client:
        res = client.get("/api/agent/runs/active", headers=headers)

    assert res.status_code == 200
    ids = [r["run_id"] for r in res.json()]
    assert "remote-1" in ids


# ---------------------------------------------------------------- json 后端


def test_json_backend_writes_jsonl(tmp_path, monkeypatch):
    """无库的降级路径也要能跨进程——写 JSONL，不重写整份文件。"""
    import json

    monkeypatch.setattr(run_events, "use_table", lambda: False)
    monkeypatch.setattr(run_events, "_DIR", tmp_path)

    run_events.append(run_id="j1", seq=1, event="step", data={"i": 0},
                      workspace_id="ws-j", owner_id="u-j")
    run_events.append(run_id="j1", seq=2, event="done", data={},
                      workspace_id="ws-j", owner_id="u-j")

    path = tmp_path / "j1.jsonl"
    assert path.exists()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    assert [x["event"] for x in lines] == ["step", "done"]

    rows = run_events.replay("j1", workspace_id="ws-j", owner_id="u-j")
    assert [e for _, e, _ in rows] == ["step", "done"]
    assert run_events.running("ws-j", "u-j") == []  # 已收尾


def test_json_backend_survives_half_written_line(tmp_path, monkeypatch):
    """进程在写某一行的中途被杀会留下半行，不能让整份日志作废。"""
    monkeypatch.setattr(run_events, "use_table", lambda: False)
    monkeypatch.setattr(run_events, "_DIR", tmp_path)

    run_events.append(run_id="j2", seq=1, event="step", data={"i": 0},
                      workspace_id="ws-j", owner_id="u-j")
    with (tmp_path / "j2.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"id": "j2:000002", "run_id": "j2", "seq": 2, "eve')  # 半行

    rows = run_events.replay("j2", workspace_id="ws-j", owner_id="u-j")
    assert len(rows) == 1
