"""核心引擎测试：事件翻译、预算续跑、中断恢复、取消、边界与配额。

**为什么这些用例以前没有**：`run_agent` / `run_deep_agent` 是 2500 行里最复杂
的三块（事件翻译、预算续跑、中断恢复），却全靠手测——于是「图表回落不留痕」
「停不掉的停止」「上下文每轮翻倍」这类问题只能等用户在界面上撞见。

这里不启真模型：用 `FakeAgent` 精确复现 LangGraph 的 `astream` 契约
（updates / messages 双模式、`__interrupt__` 键、GraphRecursionError），
把引擎的行为钉死。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.services import run_registry
from app.services.agent_deep import (
    PPT_DELIVERY_PROMPT,
    WorkspaceBackend,
    _iter_events,
    _run_engine,
    delivery_prompt,
)
from app.services.capabilities import AgentContext


class FakeAgent:
    """按脚本回放的假图：每次 astream 消费一段脚本。

    脚本元素要么是 `(mode, payload)` 列表（正常产出），要么是异常实例
    （在迭代开始时抛出，与 LangGraph 触顶的表现一致）。
    """

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[Any] = []

    async def astream(self, payload, stream_mode=None, config=None):  # noqa: ANN001, ANN201
        self.calls.append(payload)
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, dict):
            # 先产出若干帧再触顶：真实的 GraphRecursionError 总是在做完一些
            # 步骤之后才发生，一开始就抛的话 carry 是空的，测不到续跑现场
            for item in step.get("yield", []):
                yield item
            if step.get("raise") is not None:
                raise step["raise"]
            return
        for item in step:
            yield item


def _ctx() -> AgentContext:
    return AgentContext(workspace_id="ws-engine-test", owner_id="u1")


def _updates(messages: list[Any], todos: Any = None) -> tuple[str, dict]:
    state: dict[str, Any] = {"messages": messages}
    if todos is not None:
        state["todos"] = todos
    return ("updates", {"model": state})


def _final_ai(text: str) -> AIMessage:
    return AIMessage(content=text)


async def _collect(agent: FakeAgent, *, stateful: bool = False) -> list[dict]:
    events = []
    async for event in _run_engine(
        agent, _ctx(), [("user", "任务")],
        pinned=[], metric_kind="agent", with_artifacts=True,
        thread_id="ws:abc" if stateful else "", stateful=stateful,
    ):
        events.append(event)
    return events


# ---------------------------------------------------------------- 事件翻译


async def test_iter_events_translates_tool_round() -> None:
    """一轮工具调用 → tool_call + answer_reset + tool_result + final。"""
    call = AIMessage(
        content="我先查一下",
        tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "call-1"}],
    )
    agent = FakeAgent([[
        ("messages", (AIMessageChunk(content="我先查一下"), {"langgraph_checkpoint_ns": "model:1"})),
        _updates([HumanMessage(content="任务"), call, ToolMessage(content="结果", name="web_search", tool_call_id="call-1")]),
        _updates([HumanMessage(content="任务"), call, ToolMessage(content="结果", name="web_search", tool_call_id="call-1"), _final_ai("做完了")]),
    ]])
    events = await _collect(agent)
    kinds = [e["kind"] for e in events]

    assert kinds[0] == "delta"
    assert "answer_reset" in kinds          # 开始调工具 → 过程中的话作废
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "final"
    assert events[-1]["text"] == "做完了"


async def test_iter_events_skips_subagent_deltas() -> None:
    """子智能体的正文不进最终答案（checkpoint_ns 含 `|`）。"""
    agent = FakeAgent([[
        ("messages", (AIMessageChunk(content="子图的草稿"), {"langgraph_checkpoint_ns": "model:1|model:2"})),
        _updates([_final_ai("主图答案")]),
    ]])
    events = await _collect(agent)
    assert [e for e in events if e["kind"] == "delta"] == []
    assert events[-1]["text"] == "主图答案"


async def test_iter_events_emits_plan_once_per_change() -> None:
    """同一份计划重复上报只推一次（工具参数与状态会各报一次）。"""
    todos = [{"content": "查资料", "status": "in_progress"}]
    agent = FakeAgent([[
        _updates([], todos=todos),
        _updates([], todos=list(todos)),
        _updates([_final_ai("好")]),
    ]])
    events = await _collect(agent)
    assert [e["kind"] for e in events].count("plan") == 1


# ---------------------------------------------------------------- 中断与恢复


async def test_interrupt_becomes_approval_required() -> None:
    """`__interrupt__` → approval_required，且带参数指纹供批准校验。"""
    payload = SimpleNamespace(value={"action_requests": [{"name": "code_run", "args": {"code": "print(1)"}}]})
    agent = FakeAgent([[("updates", {"__interrupt__": [payload]})]])
    events = await _collect(agent)
    approval = next(e for e in events if e["kind"] == "approval_required")
    assert approval["tool"] == "code_run"
    assert approval["args_hash"]
    assert events[-1]["kind"] == "final"


# ---------------------------------------------------------------- 预算续跑


async def test_budget_continuation_without_checkpoint_keeps_single_task() -> None:
    """无检查点时续跑必须自带完整历史，但**不能把本轮任务拼两遍**。

    旧实现是 `base_messages + carry['messages']`，而 carry 里已经包含本轮
    任务消息 —— 于是输入开头出现两条一样的用户消息，每轮预算再叠一层。
    """
    first_round = AIMessage(content="做了一半")
    agent = FakeAgent([
        {
            "yield": [_updates([HumanMessage(content="任务"), first_round])],
            "raise": GraphRecursionError("顶了"),
        },
        [_updates([_final_ai("收尾")])],
    ])
    await _collect(agent, stateful=False)

    second = agent.calls[1]
    messages = second["messages"]
    tasks = [m for m in messages if getattr(m, "content", None) == "任务"]
    assert len(tasks) == 1, "本轮任务只能出现一次"
    assert isinstance(messages[-1], tuple) and messages[-1][0] == "user"
    assert "预算" in messages[-1][1]


async def test_budget_continuation_with_checkpoint_only_sends_note() -> None:
    """有检查点时图状态里已有全部对话：只补一条续接指令，不重灌历史。

    `messages` 状态用的是 add_messages（append 语义），重发历史等于把同一段
    上下文塞两份 —— 三轮预算下来 prompt_tokens 会翻几倍。
    """
    agent = FakeAgent([
        {
            "yield": [_updates([HumanMessage(content="任务"), AIMessage(content="做了一半")])],
            "raise": GraphRecursionError("顶了"),
        },
        [_updates([_final_ai("收尾")])],
    ])
    await _collect(agent, stateful=True)

    second = agent.calls[1]
    assert second["messages"] == [("user", second["messages"][0][1])]
    assert len(second["messages"]) == 1


async def test_budget_exhausted_still_delivers() -> None:
    """三轮预算全耗尽也要给一个收尾答案，而不是抛错。"""
    agent = FakeAgent([
        GraphRecursionError("顶"),
        GraphRecursionError("顶"),
        GraphRecursionError("顶"),
        GraphRecursionError("顶"),   # 保险：不该被消费到
    ])
    events = await _collect(agent, stateful=True)
    assert events[-1]["kind"] == "final"
    assert "续跑" in events[-1]["text"], "预算耗尽也要把现有成果交付并说明原因"


# ---------------------------------------------------------------- 取消


async def test_cancel_marks_reason_and_is_owner_scoped() -> None:
    record = run_registry.create(session_id="s-cancel", workspace_id="ws-cancel", owner_id="u1")

    # 别人的 run_id 一律当作不存在
    assert run_registry.cancel(record.run_id, workspace_id="ws-cancel", owner_id="u2") == "missing"

    async def _idle() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(_idle())
    run_registry.attach_task(record.run_id, task)
    try:
        assert run_registry.cancel(record.run_id, workspace_id="ws-cancel", owner_id="u1") == "cancelled"
        assert record.cancel_reason == "user"
        await asyncio.sleep(0)
        assert task.cancelled() or task.cancelling()
    finally:
        task.cancel()
        run_registry._TASKS.pop(record.run_id, None)


async def test_cancel_reports_remote_when_task_lives_elsewhere() -> None:
    """多副本下没有本地 task：必须说「停不了」，不能假装停掉了。"""
    record = run_registry.create(session_id="s-remote", workspace_id="ws-remote", owner_id="u1")
    try:
        assert run_registry.cancel(record.run_id, workspace_id="ws-remote", owner_id="u1") == "remote"
    finally:
        run_registry._RUNS.pop(record.run_id, None)


# ---------------------------------------------------------------- 代码执行隔离


def test_code_exec_refuses_host_without_explicit_permission(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.capabilities import code_exec

    monkeypatch.setattr(code_exec, "docker_ready", lambda *a, **k: (False, "no docker"))
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_SANDBOX", "auto")
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", "false")
    # 本机 .env 可能配了云沙箱；这里要验的是「全都不可用」时的拒绝姿态
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_E2B_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert code_exec.resolve_mode() == "unavailable"
        ok, why = code_exec.available()
        assert not ok and "沙箱" in why

        # 显式允许后才降级宿主
        monkeypatch.setenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", "true")
        get_settings.cache_clear()
        assert code_exec.resolve_mode() == "host"
    finally:
        monkeypatch.delenv("APP_AGENT_CODE_EXEC_SANDBOX", raising=False)
        monkeypatch.delenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", raising=False)
        get_settings.cache_clear()


def test_code_exec_container_mode_prefers_docker(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.capabilities import code_exec

    monkeypatch.setattr(code_exec, "docker_ready", lambda *a, **k: (True, "29.0"))
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_SANDBOX", "container")
    get_settings.cache_clear()
    try:
        assert code_exec.resolve_mode() == "container"
    finally:
        monkeypatch.delenv("APP_AGENT_CODE_EXEC_SANDBOX", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------- 后端边界


@pytest.fixture()
def backend(tmp_path, monkeypatch):
    """临时工作区 + 一个项目，避免碰真实的 data/projects。"""
    from app.services.capabilities import sandbox

    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(sandbox, "workspace_root", lambda ws: root)
    project = root / "demo"
    project.mkdir()
    ctx = AgentContext(workspace_id="ws-test", owner_id="u1", project="demo")
    return WorkspaceBackend(str(root), ctx), ctx, project


def test_upload_files_validates_before_writing(backend) -> None:
    """越界文件不落盘：父类「先写后报错」等于报错了也写进去。"""
    be, _ctx_, project = backend
    results = be.upload_files([
        ("/demo/ok.txt", b"hello"),
        ("/other/bad.txt", b"nope"),
    ])
    assert results[0].error is None
    assert results[1].error
    assert (project / "ok.txt").read_bytes() == b"hello"
    assert not (project.parent / "other" / "bad.txt").exists(), "越界文件不该被写到磁盘上"


def test_delete_removes_artifact_entry(backend) -> None:
    """删文件后产物清单要同步，否则前端卡片指向不存在的文件。"""
    be, ctx, _project = backend
    be.write("/demo/report.md", "# hi")
    assert [a["path"] for a in ctx.artifacts] == ["report.md"]

    be.delete("/demo/report.md")
    assert ctx.artifacts == []


def test_mcp_tool_id_parsing_is_defensive() -> None:
    """脏 id 不该炸掉整个 run：跳过即可。"""
    from app.services.tools import build_lc_tools

    tools = build_lc_tools(["mcp:only-two-parts", "mcp::noname", "calculator"], [], "ws-x", None)
    assert [t.name for t in tools] == ["calculator"]


# ---------------------------------------------------------------- 配额与提示词


def test_llm_timer_records_daily_quota() -> None:
    """智能体的 token 要进日配额账本，否则 quota_check 永远读到 0。"""
    from app.services import ratelimit
    from app.services.agent_runtime import LlmTimer

    ws = "ws-quota-engine-test"
    before = ratelimit.quota_usage(ws, "tokens")
    with LlmTimer("agent", "m", workspace_id=ws) as timer:
        timer.tokens(100, 20)
    assert ratelimit.quota_usage(ws, "tokens") == before + 120


def test_ppt_prompt_only_when_ppt_tools_mounted() -> None:
    """PPT 路线只在挂了演示文稿工具时注入：别的任务不该吃这 900 字。"""
    assert delivery_prompt(["web_search", "fs_write"]) == ""
    assert delivery_prompt(None) == ""
    assert delivery_prompt(["ppt_create"]) == PPT_DELIVERY_PROMPT
    assert delivery_prompt(["slide_upsert"]) == PPT_DELIVERY_PROMPT
    assert "slidep" not in __import__("app.services.agent_deep", fromlist=["x"]).DEEP_SYSTEM_PROMPT


def test_code_run_tool_not_registered_without_sandbox(monkeypatch) -> None:
    """没有沙箱又没允许宿主时，code_run 干脆不挂。

    挂上的话模型会先试一次、拿到「已拒绝执行」，白跑一轮还打断计划；
    用户会在开跑时收到一条 note（见 _run_deep），不是无声消失。
    """
    from app.core.config import get_settings
    from app.services.capabilities import code_exec, make_tools

    monkeypatch.setattr(code_exec, "docker_ready", lambda *a, **k: (False, "no docker"))
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", "false")
    # 本机 .env 可能配了云沙箱；测试要的是「一个沙箱都没有」的世界
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_E2B_API_KEY", "")
    get_settings.cache_clear()
    try:
        names = {t.name for t in make_tools(_ctx(), ["code_run"])}
        assert "code_run" not in names
    finally:
        monkeypatch.delenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", raising=False)
        get_settings.cache_clear()


async def test_usage_collected_from_stream_chunks(monkeypatch) -> None:
    """deep 引擎的 token 用量来自 messages 流的 usage_metadata。

    这条链路以前根本没接：智能体走 ChatOpenAI 直连，token 只进 metrics，
    日配额永远读到 0 —— 配了额度也不拦截。
    """
    from app.services import agent_deep

    recorded: list[tuple[int, int]] = []

    class SpyTimer(agent_deep.LlmTimer):
        def tokens(self, prompt, completion):  # noqa: ANN001
            recorded.append((prompt, completion))
            super().tokens(prompt, completion)

    monkeypatch.setattr(agent_deep, "LlmTimer", SpyTimer)

    chunk = AIMessageChunk(
        content="好的", id="chunk-1",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    agent = FakeAgent([
        {
            "yield": [("messages", (chunk, {"langgraph_checkpoint_ns": "model:1"}))],
            "raise": GraphRecursionError("顶"),
        },
        [_updates([_final_ai("收尾")])],
    ])
    await _collect(agent, stateful=True)
    assert recorded and recorded[-1] == (10, 5)


async def test_usage_not_double_counted_between_stream_and_updates(monkeypatch) -> None:
    """同一轮用量不能既从流里算一次、又从 updates 里算一次。"""
    from app.services import agent_deep

    recorded: list[tuple[int, int]] = []

    class SpyTimer(agent_deep.LlmTimer):
        def tokens(self, prompt, completion):  # noqa: ANN001
            recorded.append((prompt, completion))
            super().tokens(prompt, completion)

    monkeypatch.setattr(agent_deep, "LlmTimer", SpyTimer)

    chunk = AIMessageChunk(
        content="好的", id="chunk-2",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    full = AIMessage(
        content="好的", id="msg-2",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    agent = FakeAgent([[
        ("messages", (chunk, {"langgraph_checkpoint_ns": "model:1"})),
        _updates([full]),
    ]])
    await _collect(agent)
    assert recorded == [(7, 3)], f"只该记一次，实际 {recorded}"


def test_code_run_tool_not_registered_without_sandbox(monkeypatch) -> None:
    """没有沙箱又没允许宿主时，code_run 干脆不挂。

    挂上的话模型会先试一次、拿到「已拒绝执行」，白跑一轮还打断计划；
    用户会在开跑时收到一条 note（见 _run_deep），不是无声消失。
    """
    from app.core.config import get_settings
    from app.services.capabilities import code_exec, make_tools

    monkeypatch.setattr(code_exec, "docker_ready", lambda *a, **k: (False, "no docker"))
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", "false")
    # 本机 .env 可能配了云沙箱；测试要的是「一个沙箱都没有」的世界
    monkeypatch.setenv("APP_AGENT_CODE_EXEC_E2B_API_KEY", "")
    get_settings.cache_clear()
    try:
        names = {t.name for t in make_tools(_ctx(), ["code_run"])}
        assert "code_run" not in names
    finally:
        monkeypatch.delenv("APP_AGENT_CODE_EXEC_ALLOW_HOST", raising=False)
        get_settings.cache_clear()


async def test_usage_collected_from_stream_chunks(monkeypatch) -> None:
    """deep 引擎的 token 用量来自 messages 流的 usage_metadata。

    这条链路以前根本没接：智能体走 ChatOpenAI 直连，token 只进 metrics，
    日配额永远读到 0 —— 配了额度也不拦截。
    """
    from app.services import agent_deep

    recorded: list[tuple[int, int]] = []

    class SpyTimer(agent_deep.LlmTimer):
        def tokens(self, prompt, completion):  # noqa: ANN001
            recorded.append((prompt, completion))
            super().tokens(prompt, completion)

    monkeypatch.setattr(agent_deep, "LlmTimer", SpyTimer)

    chunk = AIMessageChunk(
        content="好的", id="chunk-1",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    agent = FakeAgent([
        {
            "yield": [("messages", (chunk, {"langgraph_checkpoint_ns": "model:1"}))],
            "raise": GraphRecursionError("顶"),
        },
        [_updates([_final_ai("收尾")])],
    ])
    await _collect(agent, stateful=True)
    assert recorded and recorded[-1] == (10, 5)


async def test_usage_not_double_counted_between_stream_and_updates(monkeypatch) -> None:
    """同一轮用量不能既从流里算一次、又从 updates 里算一次。"""
    from app.services import agent_deep

    recorded: list[tuple[int, int]] = []

    class SpyTimer(agent_deep.LlmTimer):
        def tokens(self, prompt, completion):  # noqa: ANN001
            recorded.append((prompt, completion))
            super().tokens(prompt, completion)

    monkeypatch.setattr(agent_deep, "LlmTimer", SpyTimer)

    chunk = AIMessageChunk(
        content="好的", id="chunk-2",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    full = AIMessage(
        content="好的", id="msg-2",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    agent = FakeAgent([[
        ("messages", (chunk, {"langgraph_checkpoint_ns": "model:1"})),
        _updates([full]),
    ]])
    await _collect(agent)
    assert recorded == [(7, 3)], f"只该记一次，实际 {recorded}"
