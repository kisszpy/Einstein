"""审批装配：什么时候给 code_run 挂中断，什么时候直接放行。

这里判错的代价两边都很大：多挂一次是每次执行都打断用户；少挂一次是把
「宿主进程里跑模型生成的代码」这道门整个拆掉。所以四条路径分开断言。
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services import agent_deep
from app.services.capabilities import AgentContext
from app.services.capabilities import code_exec


@pytest.fixture(autouse=True)
def _tools_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_AGENT_APPROVAL_TOOLS", "code_run")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ctx(**kw) -> AgentContext:
    ctx = AgentContext(workspace_id="ws-a", owner_id="u-a")
    for key, value in kw.items():
        setattr(ctx, key, value)
    return ctx


def test_isolated_sandbox_is_not_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    """云沙箱/容器里跑代码不问人，也不打开 hitl。

    plan 为空却把 hitl 置真，会让工具层也跳过自己的检查——等于两道门同时
    敞开，这是最容易写错的一处。
    """
    monkeypatch.setattr(code_exec, "needs_approval", lambda: False)
    ctx = _ctx()
    assert agent_deep._approval_plan(ctx) is None
    assert ctx.hitl is False


def test_host_execution_always_interrupts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_exec, "needs_approval", lambda: True)
    ctx = _ctx()
    plan = agent_deep._approval_plan(ctx)
    assert plan is not None and "code_run" in plan
    assert ctx.hitl is True
    # 必须是带谓词的配置，不是裸 True：环境判定要在中断那一刻求值
    assert plan["code_run"]["allowed_decisions"] == ["approve", "reject"]
    assert plan["code_run"]["when"](None) is True


def test_session_grant_skips_new_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """点过「本次会话都允许」之后，新发起的运行不再挂中断。"""
    monkeypatch.setattr(code_exec, "needs_approval", lambda: True)
    ctx = _ctx(approved_tools={"code_run"})
    assert agent_deep._approval_plan(ctx) is None
    assert ctx.hitl is False


def test_resume_still_mounts_the_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """续跑那一次必须挂：检查点里还有待确认的调用，图少了节点就永远走不下去。

    这是「会话授权」最容易写崩的地方——豁免条件如果没排除 resuming，用户点完
    批准反而会卡死在这个 thread 上。
    """
    monkeypatch.setattr(code_exec, "needs_approval", lambda: True)
    ctx = _ctx(approved_tools={"code_run"}, resuming=True)
    plan = agent_deep._approval_plan(ctx)
    assert plan is not None and "code_run" in plan
    assert ctx.hitl is True


def test_empty_config_disables_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_AGENT_APPROVAL_TOOLS", "")
    get_settings.cache_clear()
    monkeypatch.setattr(code_exec, "needs_approval", lambda: True)
    ctx = _ctx()
    assert agent_deep._approval_plan(ctx) is None
    assert ctx.hitl is False
