"""长期记忆接入智能体：召回到的内容必须真的进系统提示。

以前只有对话链路带记忆，智能体每次都从零开始——同一个工作区里已经沉淀的
事实（"我们用 Postgres 15"），问对话知道，派任务就不知道。
"""

from __future__ import annotations

from app.services.agent_runtime import memory_block, recall_memory
from app.services.capabilities import AgentContext


def _ctx() -> AgentContext:
    return AgentContext(workspace_id="ws-memory-test", owner_id="u-memory")


def test_recall_finds_related_fact():
    from app.services import memory as memory_service

    memory_service.add_memory("ws-memory-test", "生产数据库是 Postgres 15，端口 5433")
    memory_service.add_memory("ws-memory-test", "周会固定在周二上午十点")

    ctx = _ctx()
    hits = recall_memory(ctx, "我们用的数据库是什么版本？")
    assert any("Postgres" in h for h in hits)
    assert ctx.memory == hits          # 同时写进 ctx，提示词构建器才拿得到


def test_recall_returns_empty_when_nothing_matches():
    from app.services import memory as memory_service

    memory_service.add_memory("ws-memory-empty", "只和报销流程有关的记忆")
    ctx = AgentContext(workspace_id="ws-memory-empty", owner_id="u")
    assert recall_memory(ctx, "Kubernetes 的探针怎么写") == []
    assert ctx.memory == []


def test_memory_block_is_empty_without_memories():
    assert memory_block([]) == ""


def test_memory_block_frames_facts_not_instructions():
    """措辞必须防两件事：逐条复述、把背景当成本轮指令。"""
    text = memory_block(["我们用 Postgres 15"])
    assert "Postgres 15" in text
    assert "不要逐条复述" in text
    assert "以本轮要求为准" in text


def test_recall_failure_does_not_break_task(monkeypatch):
    """记忆读失败只能降级为"没有背景"，不能让整个任务起不来。"""
    from app.services import memory as memory_service

    monkeypatch.setattr(
        memory_service, "relevant_memory",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    ctx = _ctx()
    assert recall_memory(ctx, "随便什么") == []
    assert ctx.memory == []


def test_system_prompt_carries_memory(monkeypatch):
    """装配出来的提示词里必须真的带上记忆段（而不只是 ctx 上有值）。"""
    from app.services import agent_deep

    captured: dict = {}

    def fake_create(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(agent_deep, "create_deep_agent", fake_create)
    ctx = _ctx()
    ctx.memory = ["我们的向量库是 Milvus，集合名 einstein_chunks"]
    agent_deep._build_agent(ctx, [], [], "ws-memory-test")

    prompt = captured["system_prompt"]
    assert "Milvus" in prompt
    assert "已知背景（来自长期记忆）" in prompt
