"""工具集与后端能力必须一致；容器权限参数必须真的传下去。

这两类问题都不会抛异常，只会"看起来正常"：
- 默认引擎给模型一个 `execute`，模型调了才拿到一句英文报错，白费一轮；
- 容器参数顺序写错（docker 的选项必须在镜像名之前），`docker run` 直接
  报错或悄悄按 root 起——静态读代码看不出来，只能断言 argv。
"""

from __future__ import annotations

from types import SimpleNamespace

from deepagents.backends import FilesystemBackend

from app.services.agent_deep import _fs_tool_names
from app.services.capabilities import AgentContext, make_tools


def _ctx() -> AgentContext:
    return AgentContext(workspace_id="ws-tools", owner_id="u-tools")


# ---------------------------------------------------------------- 工具集


def test_default_backend_has_no_execute(tmp_path):
    """默认引擎没有执行能力，就不该挂 execute。"""
    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    names = _fs_tool_names(backend)
    assert "execute" not in names
    # 文件工具一个都不能少：read_file 是 harness 的必需工具
    assert "read_file" in names and "write_file" in names and "grep" in names


def test_execute_capable_backend_gets_execute(tmp_path):
    from app.services.agent_deep_pro import ProBackend

    backend = ProBackend(str(tmp_path), _ctx(), mode="off")
    assert "execute" in _fs_tool_names(backend)


def test_capability_group_id_is_not_a_tool_id():
    """`code` 是能力分组，不是工具 id —— 传它不会挂上 code_run。

    这个坑真实存在：前端把分组展开成工具 id 再传，手写请求时容易直接传
    `["code"]`，表现为"模型明明该用代码执行，却一次都没调"。
    """
    assert make_tools(_ctx(), ["code"]) == []
    names = {t.name for t in make_tools(_ctx(), ["code_run"])}
    assert "code_run" in names


def test_python_execution_requires_approval_when_hitl_on(monkeypatch):
    """开了 HITL 之后工具本身不再抛异常：拦截在框架层，工具保持"被调用即已批准"。"""
    from app.core.config import get_settings
    from app.services.capabilities import ApprovalRequired

    monkeypatch.setenv("APP_AGENT_APPROVAL_TOOLS", "code_run")
    get_settings.cache_clear()
    try:
        ctx = _ctx()
        ctx.hitl = True            # 引擎装配时置真
        tool = next(t for t in make_tools(ctx, ["code_run"]) if t.name == "code_run")
        result = tool.invoke({"code": "print(1)", "language": "python"})
        assert "1" in str(result)

        ctx.hitl = False           # 没有 interrupt 的引擎（react）走异常路径
        tool = next(t for t in make_tools(ctx, ["code_run"]) if t.name == "code_run")
        try:
            tool.invoke({"code": "print(2)", "language": "python"})
        except ApprovalRequired:
            pass
        else:  # pragma: no cover - 失败时给出可读信息
            raise AssertionError("未批准的 code_run 应当被拦下")
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- 容器参数


def test_container_argv_order_and_permissions(tmp_path, monkeypatch):
    """非 root + 只读根 + tmpfs，且所有选项都在镜像名之前。"""
    from app.services.agent_deep_pro import ProBackend

    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = list(argv)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.services.agent_deep_pro.subprocess.run", fake_run)
    backend = ProBackend(str(tmp_path), _ctx(), mode="container", run_as="1000:1000")
    backend._run_container("echo hi", tmp_path, 10, as_root=False)
    argv = captured["argv"]

    image_at = argv.index(backend.image)
    for flag in ("--read-only", "--user", "--cap-drop", "--security-opt", "--tmpfs"):
        assert flag in argv, flag
        assert argv.index(flag) < image_at, f"{flag} 必须在镜像名之前，否则 docker 会当成命令参数"
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert "PIP_USER=1" in argv          # 只读根下装依赖的唯一出路
    assert argv[-2:] == ["sh", "-s"]     # 命令仍走 stdin


def test_container_root_fallback_drops_user_flag(tmp_path, monkeypatch):
    """属主不匹配时的降级路径：去掉 --user 重试，其余隔离保持不变。"""
    from app.services.agent_deep_pro import ProBackend

    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = list(argv)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.services.agent_deep_pro.subprocess.run", fake_run)
    backend = ProBackend(str(tmp_path), _ctx(), mode="container", run_as="1000:1000")
    backend._run_container("echo hi", tmp_path, 10, as_root=True)
    assert "--user" not in captured["argv"]
    assert "--read-only" in captured["argv"]


def test_readonly_root_can_be_disabled(tmp_path, monkeypatch):
    from app.services.agent_deep_pro import ProBackend

    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = list(argv)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.services.agent_deep_pro.subprocess.run", fake_run)
    backend = ProBackend(
        str(tmp_path), _ctx(), mode="container", readonly_root=False,
    )
    backend._run_container("echo hi", tmp_path, 10, as_root=False)
    assert "--read-only" not in captured["argv"]


# ---------------------------------------------------------------- 项目 slug


def test_slug_rejects_pure_punctuation():
    """纯符号的项目名不能静默落到 untitled（那会让两个项目撞名）。"""
    from app.services.capabilities.sandbox import slug_from_name, slugify

    assert slug_from_name("。。。！！！") == ""
    assert slugify("。。。！！！") == "untitled"   # 仅内部路径兜底
    assert slug_from_name("周报 Q4") != ""
    assert slugify("周报 Q4").startswith("周报")
