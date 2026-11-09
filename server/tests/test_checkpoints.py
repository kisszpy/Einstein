"""短期记忆（LangGraph 线程状态）落盘。

这里验的是「服务重启之后，批准还能从断点续跑」这件事的前置条件：检查点必须
真的在磁盘上，而不是只在那个进程的内存里。

所以第二个用例刻意关掉连接再重新打开——`close()` 之后 `acquire()` 拿到的是
一条全新的连接，只有真落盘才读得回上一次的图状态。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import checkpoints


@pytest.fixture(autouse=True)
async def _reset_saver():
    """每个用例前后都关掉单例：sqlite 档会绑定事件循环，串用会报 loop 不一致。"""
    await checkpoints.close()
    yield
    await checkpoints.close()


@pytest.fixture()
def sqlite_mode(monkeypatch, tmp_path):
    from app.core.config import get_settings

    path = tmp_path / "ckpt.db"
    monkeypatch.setenv("APP_AGENT_CHECKPOINT", "sqlite")
    monkeypatch.setenv("APP_AGENT_CHECKPOINT_SQLITE_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _settings_env(monkeypatch, **env: str) -> None:
    from app.core.config import get_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


# ------------------------------------------------------------------ 档位解析
def test_auto_follows_store_backend(monkeypatch):
    """auto 不该是个独立开关：主库换了，检查点跟着换，运维不用记两处。"""
    monkeypatch.setattr(checkpoints, "psycopg_async_supported", lambda: True)
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="auto", APP_STORE_BACKEND="postgres")
    assert checkpoints.resolve_mode() == "postgres"

    _settings_env(monkeypatch, APP_STORE_BACKEND="sqlite")
    assert checkpoints.resolve_mode() == "sqlite"

    # json 主库也是单机形态，没有理由不用 sqlite 落检查点
    _settings_env(monkeypatch, APP_STORE_BACKEND="json")
    assert checkpoints.resolve_mode() == "sqlite"


def test_auto_avoids_postgres_where_psycopg_cannot_run(monkeypatch):
    """Windows：psycopg 用不了 Proactor 循环，auto 必须直接给 sqlite。

    否则每次启动都要白等 15 秒连接池超时再降级，日志还刷一堆
    "cannot use the ProactorEventLoop"。
    """
    monkeypatch.setattr(checkpoints, "psycopg_async_supported", lambda: False)
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="auto", APP_STORE_BACKEND="postgres")
    assert checkpoints.resolve_mode() == "sqlite"

    # 但显式指定 postgres 时保留用户意图（真连不上再降级，别悄悄改配置）
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="postgres")
    assert checkpoints.resolve_mode() == "postgres"


def test_explicit_modes_win(monkeypatch):
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="postgres", APP_STORE_BACKEND="sqlite")
    assert checkpoints.resolve_mode() == "postgres"

    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="memory")
    assert checkpoints.resolve_mode() == "memory"

    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="off")
    assert checkpoints.resolve_mode() == "off"


def test_unknown_value_falls_back(monkeypatch):
    """配错字符串时退回 auto，而不是静默变成 off——后者会让续跑悄悄消失。"""
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="postgre", APP_STORE_BACKEND="sqlite")
    assert checkpoints.resolve_mode() == "sqlite"


async def test_off_returns_none(monkeypatch):
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="off")
    assert await checkpoints.acquire() is None


async def test_memory_mode_uses_process_saver(monkeypatch):
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="memory")
    saver = await checkpoints.acquire()
    assert saver is checkpoints.memory_saver()
    # 同一进程内反复取用必须是同一个实例，否则写进去的检查点读不回来
    assert await checkpoints.acquire() is saver


# ------------------------------------------------------------------ 真落盘
def _graph(saver):
    """一个最小的单节点图：只为验证状态能不能跨连接留下来。"""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    def reply(state):
        return {"messages": [AIMessage(content="收到")]}

    builder = StateGraph(MessagesState)
    builder.add_node("reply", reply)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=saver)


async def test_sqlite_checkpoint_survives_reconnect(sqlite_mode):
    """关掉连接再打开，历史还在——这是"重启后能续跑"的最小证明。"""
    config = {"configurable": {"thread_id": "ws-1:session-1"}}

    saver = await checkpoints.acquire()
    await _graph(saver).ainvoke({"messages": [("user", "第一句")]}, config)

    await checkpoints.close()          # 等价于进程退出
    saver2 = await checkpoints.acquire()
    assert saver2 is not saver, "应是一条新连接，否则这个用例证明不了落盘"

    state = await _graph(saver2).aget_state(config)
    texts = [m.content for m in state.values.get("messages", [])]
    assert "第一句" in texts
    assert sqlite_mode.exists(), "sqlite 文件应该已经生成"


async def test_sqlite_threads_are_isolated(sqlite_mode):
    """不同 thread 之间不能串：会话 id 是隔离边界。"""
    saver = await checkpoints.acquire()
    graph = _graph(saver)

    a = {"configurable": {"thread_id": "ws-1:session-a"}}
    b = {"configurable": {"thread_id": "ws-1:session-b"}}
    await graph.ainvoke({"messages": [("user", "A 的话")]}, a)
    await graph.ainvoke({"messages": [("user", "B 的话")]}, b)

    texts_a = [m.content for m in (await graph.aget_state(a)).values["messages"]]
    texts_b = [m.content for m in (await graph.aget_state(b)).values["messages"]]
    assert "A 的话" in texts_a and "B 的话" not in texts_a
    assert "B 的话" in texts_b and "A 的话" not in texts_b


# ------------------------------------------------------------------ 降级
async def test_postgres_unavailable_falls_back_to_sqlite(monkeypatch, sqlite_mode):
    """连接不上时先退 sqlite，而不是直接掉到内存。

    掉内存等于"什么都没落"，用户看不到任何异常，直到某天批准之后发现任务
    被重跑了一遍——降级的落点必须尽量还落在盘上。
    """
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT="postgres", APP_POSTGRES_DSN="")
    saver = await checkpoints.acquire()
    assert type(saver).__name__ == "AsyncSqliteSaver"
    assert checkpoints.describe()["mode"] == "postgres"   # 配置仍是用户写的那个
    assert checkpoints.describe()["active"] == "sqlite"   # 实际在跑的是降级档


async def test_memory_fallback_when_nothing_works(monkeypatch, tmp_path):
    """连 sqlite 也打不开时才退内存，并靠日志说清楚后果。"""
    # 用一个"文件"当目录：建库必然失败
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    _settings_env(
        monkeypatch, APP_AGENT_CHECKPOINT="sqlite",
        APP_AGENT_CHECKPOINT_SQLITE_PATH=str(blocked / "ckpt.db"),
    )
    saver = await checkpoints.acquire()
    assert saver is checkpoints.memory_saver()


# ------------------------------------------------------------------ 清理
class _FakeSaver:
    """只实现清理用到的两个方法，避免为了测 TTL 去起真库。"""

    def __init__(self, rows: list[tuple[str, str]]):
        self.rows = rows
        self.deleted: list[str] = []

    async def alist(self, config, *, limit=None):
        for thread_id, ts in self.rows:
            yield SimpleNamespace(
                config={"configurable": {"thread_id": thread_id}},
                checkpoint={"ts": ts},
            )

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


async def test_purge_removes_only_expired_threads(monkeypatch):
    """判据是 checkpoint 自带的最新 ts，不是文件时间——后者永远删不掉东西。"""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=5)).isoformat()
    fresh = (now - timedelta(minutes=5)).isoformat()

    fake = _FakeSaver([
        ("ws:old", old),
        ("ws:fresh", fresh),
        ("ws:old", old),        # 同一线程多条，只应删一次
        ("ws:broken", ""),      # ts 解析不出来时宁可留着
    ])
    monkeypatch.setattr(checkpoints, "acquire", lambda: _async_value(fake))
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT_TTL_HOURS="72")

    removed = await checkpoints.purge_expired()
    assert removed == 1
    assert fake.deleted == ["ws:old"]


async def test_purge_disabled_when_ttl_zero(monkeypatch):
    """TTL=0 表示不清理：不能因为默认值写错就把活跃会话删光。"""
    fake = _FakeSaver([("ws:old", "2020-01-01T00:00:00+00:00")])
    monkeypatch.setattr(checkpoints, "acquire", lambda: _async_value(fake))
    _settings_env(monkeypatch, APP_AGENT_CHECKPOINT_TTL_HOURS="0")

    assert await checkpoints.purge_expired() == 0
    assert fake.deleted == []


def _async_value(value):
    """把同步返回值包成可 await 的——purge 里是 `await acquire()`。"""

    async def _inner():
        return value

    return _inner()


# ------------------------------------------------------------------ 可观测性
def test_describe_reports_target(monkeypatch):
    _settings_env(
        monkeypatch, APP_AGENT_CHECKPOINT="sqlite",
        APP_AGENT_CHECKPOINT_SQLITE_PATH="data/ckpt.db",
    )
    info = checkpoints.describe()
    assert info["mode"] == "sqlite"
    assert info["target"].endswith("ckpt.db")
    assert info["ttl_hours"] >= 0
