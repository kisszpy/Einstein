"""Deep Agents Pro 验收：沙箱（容器/本地）、项目边界、声明式权限、浏览器、引擎接缝。

跑法：`python tools/verify_deep_pro.py`
不依赖大模型（只构建 agent，不调 LLM），所以能进 CI。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.capabilities import AgentContext, browser_pro, sandbox
from app.services.capabilities.sandbox import slugify, workspace_root

WS = "ws-pro-verify"
PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    mark = "PASS " if ok else "FAIL "
    print(f"{mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def skip(name: str, why: str) -> None:
    SKIP.append(name)
    print(f"SKIP  {name} — {why}")


def purge(root: Path) -> None:
    if not root.exists():
        return
    for cur, dirs, files in os.walk(root, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(cur, name))
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(cur, name))
            except OSError:
                pass
    try:
        os.rmdir(root)
    except OSError:
        pass


def main() -> int:
    from app.services.agent_deep_pro import ProBackend, docker_ready

    root = workspace_root(WS)
    purge(root)
    proj = root / "proj-a"
    proj.mkdir(parents=True, exist_ok=True)
    other = root / "proj-b"
    other.mkdir(parents=True, exist_ok=True)

    ok_docker, docker_note = docker_ready()
    print(f"docker: {'可用 ' + docker_note if ok_docker else '不可用（' + docker_note + '）'}\n")

    ctx = AgentContext(workspace_id=WS, owner_id="u-pro-verify", project="proj-a")
    backend = ProBackend(str(root), ctx, mode="auto", timeout=60, network="bridge")

    check("模式解析：auto 选到容器或明确降级", backend.mode in ("container", "local"), backend.mode)
    if ok_docker:
        check("auto 模式优先容器", backend.mode == "container", f"{backend.mode} / {backend.mode_note}")
    else:
        check("无 docker 时降级并给出原因", backend.mode == "local" and bool(backend.mode_note), backend.mode_note)

    # ---------- shell ----------
    r = backend.execute("echo pro-shell-ok")
    check("execute 基本命令", r.exit_code == 0 and "pro-shell-ok" in r.output, r.output[:200])

    r = backend.execute("pwd && ls -a")
    check("execute 工作目录锁定在项目", ("/work" in r.output) or (str(proj) in r.output), r.output[:200])

    r = backend.execute("python3 -c \"print(6*7)\"")
    check("容器/宿主内有 python3", r.exit_code == 0 and "42" in r.output, r.output[:200])

    r = backend.execute("echo from-shell > shell.txt && cat shell.txt")
    on_disk = (proj / "shell.txt").is_file()
    check("execute 落盘到项目目录", on_disk and "from-shell" in r.output, f"disk={on_disk}")

    # 命令黑名单只对宿主 shell 生效：容器有真隔离，黑名单只会误杀
    local = ProBackend(str(root), ctx, mode="local", timeout=60)
    r = local.execute("rm -rf /")
    check("宿主模式拦危险命令", r.exit_code == 126 and "已拒绝" in r.output, r.output[:120])
    if backend.mode == "container":
        r = backend.execute("shutdown -h now")
        check("容器模式不靠正则黑名单（隔离兜底）", r.exit_code != 126, f"exit={r.exit_code}")

    r = backend.execute("exit 3")
    check("非零退出码如实上报", r.exit_code == 3, f"exit={r.exit_code} out={r.output[:80]}")

    # 超时
    r = backend.execute("sleep 5", timeout=5)
    check("超时被终止", r.exit_code in (124, 143, 137), f"exit={r.exit_code}")

    # ---------- 项目边界 ----------
    w = backend.write("/proj-b/evil.txt", "x")
    check("写别的项目被拒", bool(getattr(w, "error", None)), str(getattr(w, "error", ""))[:100])

    w = backend.write("/.agent/skills/x/SKILL.md", "x")
    check("写运行时目录被拒", bool(getattr(w, "error", None)), str(getattr(w, "error", ""))[:100])

    w = backend.write("/proj-a/ok.txt", "hello")
    check("写自己项目成功", getattr(w, "error", None) is None, str(getattr(w, "error", ""))[:100])
    check("产物已登记", any(a["path"] == "ok.txt" for a in ctx.artifacts), str(ctx.artifacts))

    rd = backend.read("/proj-b/evil.txt")
    check("读别的项目被拒", bool(getattr(rd, "error", None)), str(getattr(rd, "error", ""))[:100])

    # ---------- 浏览器 ----------
    caps = browser_pro.capabilities()
    check("浏览器能力自述", caps["http"] and "browser_open" in caps["tools"], str(caps))

    html = '<html><head><title>t</title></head><body><a href="/a">Apple</a><a href="https://x.test/b">Banana</a><a href="#top">skip</a></body></html>'
    rows = browser_pro.parse_links(html, "https://x.test/dir/page")
    check(
        "链接解析（相对/绝对/锚点）",
        len(rows) == 2 and rows[0]["url"] == "https://x.test/a" and rows[1]["url"] == "https://x.test/b",
        str(rows),
    )

    try:
        page = browser_pro.open_page("https://example.com", max_chars=500)
        check("browser_open 抓取公网页面", "Example" in (page["title"] + page["text"]), page["title"][:80])
    except Exception as exc:  # noqa: BLE001
        skip("browser_open 抓取公网页面", f"网络不可用（{exc}）")

    if caps["screenshot"]:
        shot = proj / "shots" / "probe.png"
        try:
            info = browser_pro.screenshot("https://example.com", shot)
            check("browser_shot 截图落盘", shot.is_file() and info["bytes"] > 1000, str(info)[:160])
        except Exception as exc:  # noqa: BLE001
            check("browser_shot 截图落盘", False, str(exc)[:160])
    else:
        skip("browser_shot 截图落盘", "无可用无头浏览器（预期降级）")

    # ---------- 工具与引擎接缝 ----------
    from app.services.agent_deep_pro import _browser_tools

    names = {t.name for t in _browser_tools(ctx, WS)}
    check("三个浏览器工具已注册", names == {"browser_open", "browser_links", "browser_shot"}, str(names))

    from deepagents.middleware.filesystem import supports_execution

    check("harness 认可后端支持 execute", supports_execution(backend), "supports_execution=False")

    # ---------- 构建 agent（不调用模型） ----------
    from app.services.agent_deep_pro import _build_pro_agent

    try:
        agent, backend2 = _build_pro_agent(ctx, [], [], WS, persona="", temperature=0.2)
        check("create_deep_agent 构建成功", agent is not None)
        check("构建时沿用同一沙箱模式", backend2.mode == backend.mode, f"{backend2.mode} vs {backend.mode}")
    except Exception as exc:  # noqa: BLE001
        check("create_deep_agent 构建成功", False, f"{type(exc).__name__}: {exc}")

    # ---------- 端到端（假模型）：模型发出的 execute 调用真的被执行 ----------
    try:
        from langchain_core.language_models import BaseChatModel
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        from pydantic import Field as PField

        class _Scripted(BaseChatModel):
            """第一轮返回 execute 调用，第二轮收尾。只用来验工具注入与执行链路。"""

            calls: int = 0
            bound: list[str] = PField(default_factory=list)

            @property
            def _llm_type(self) -> str:
                return "scripted"

            def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
                self.bound = [getattr(t, "name", "") for t in tools]
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
                self.calls += 1
                if self.calls == 1:
                    msg = AIMessage(
                        content="",
                        tool_calls=[{
                            "name": "execute",
                            "args": {"command": "echo e2e-harness-ok"},
                            "id": "call_e2e",
                        }],
                    )
                else:
                    msg = AIMessage(content="端到端完成")
                return ChatResult(generations=[ChatGeneration(message=msg)])

        from deepagents import create_deep_agent
        from langchain.agents.middleware import TodoListMiddleware

        model = _Scripted()
        agent = create_deep_agent(
            model=model,
            tools=[],
            system_prompt="verify",
            backend=backend,
            middleware=[TodoListMiddleware()],
        )
        result = agent.invoke(
            {"messages": [("user", "跑一下 echo")]},
            config={"recursion_limit": 20},
        )
        msgs = result.get("messages") or []
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        check("模型绑定的工具里有 execute", "execute" in model.bound, str(model.bound))
        check(
            "execute 调用被真正执行",
            any("e2e-harness-ok" in str(m.content) for m in tool_msgs),
            str([m.content for m in tool_msgs])[:200],
        )
    except Exception as exc:  # noqa: BLE001
        check("端到端（假模型）链路", False, f"{type(exc).__name__}: {exc}")

    # 两个引擎共用同一套执行循环（不是复制一份）
    from app.services import agent_deep, agent_deep_pro

    check("旧引擎入口仍在", callable(agent_deep.run_deep_agent))
    check(
        "deep 与 pro 共用 _run_deep / WorkspaceBackend",
        agent_deep_pro._run_deep is agent_deep._run_deep
        and issubclass(agent_deep_pro.ProBackend, agent_deep.WorkspaceBackend),
        "执行循环被复制了",
    )

    # ---------- 清理 ----------
    purge(root)
    if not any(root.parent.glob(f"{slugify(WS)}*")):
        print("\n清理完成")

    print(f"\nPASS {len(PASS)} / FAIL {len(FAIL)} / SKIP {len(SKIP)}")
    if FAIL:
        print("失败项：\n  " + "\n  ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
