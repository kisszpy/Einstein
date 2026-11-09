"""Deep Agents Pro：harness 原生能力 + 真沙箱 + 浏览器。

和 `agent_deep.py` 的关系：**只做增量，不复制**。事件循环、预算续跑、审批
暂停、消息拼装、agent 装配全部复用 `agent_deep` 的公共实现（`_run_deep` /
`_build_agent` / `WorkspaceBackend`），这里只做三件事：

1. **换后端**：`ProBackend` 多继承 `SandboxBackendProtocol`，harness 检测到
   后端支持 execute 就会多给模型一个 `execute` 工具（见
   `deepagents.middleware.filesystem.supports_execution`）。
   - 容器模式：`docker run --rm`，项目目录挂 `/work`，`--cap-drop ALL`、
     内存/CPU/PID 全限；命令用 `sh -s` 从 stdin 读脚本，避免 `sh -lc "..."`
     的双层引号解析。超时后补刀 `docker rm -f`。
   - 宿主降级：执行交给框架的 `LocalShellBackend` —— 环境变量、超时、
     输出归并、退出码、截断都由它负责，这里不再自己写 subprocess 包装。
2. **加工具**：`browser_open` / `browser_links` / `browser_shot`，用
   langchain 的 `@tool` 从签名与 docstring 生成 schema，不手写 pydantic 模型。
3. **加提示词**：`PRO_EXTRA` 说明执行环境，其余提示词由公共构建器拼装。

输出格式的约定也交给框架：`ExecuteResponse` 只回原始 stdout/stderr 与退出码，
harness 会补上 `[Command succeeded/failed with exit code N]` 与截断说明
（`middleware/filesystem.py:3159`）。这里再拼一遍就会出现两条状态行。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain_core.tools import tool

from app.core.config import get_settings
from app.services.agent_deep import (
    WorkspaceBackend,
    _approval_plan,
    _build_agent,
    _run_deep,
)
from app.services.capabilities import AgentContext, _artifact_kind, browser_pro, sandbox
from app.services.capabilities.code_exec import _clean_env
from app.services.capabilities.sandbox import slugify, workspace_root

MAX_SHELL_TIMEOUT = 300
MAX_OUTPUT_BYTES = 100_000   # 与框架 LocalShellBackend 的默认上限一致

# 宿主 shell 模式（无隔离）下才拦：这些命令足以毁掉整台机器。容器模式不拦，
# 隔离由 --rm / 只挂项目目录 / cap-drop 负责——用正则做命令黑名单本来就挡不住
# 有心绕过的人，拦住的只是自己。
_DANGEROUS = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME)(\s|$)", "递归删除根目录/家目录"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\b[^\n]*\bof=/dev/", "直接写块设备"),
    (r":\s*\(\s*\)\s*\{.*\}\s*;\s*:", "fork 炸弹"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "关机/重启"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\b(del|rmdir)\s+/[sq]\b|\brd\s+/s\b", "Windows 递归删除"),
    (r"\breg\s+delete\b", "删除注册表"),
    (r"\bdocker\s+(rm|kill|stop|prune)\b", "动宿主容器"),
    (r"\bdocker\b[^\n]*--privileged", "特权容器"),
]

_DOCKER_PROBE: dict[str, Any] = {"checked": False, "ok": False, "reason": ""}


def docker_ready(probe_timeout: int = 6) -> tuple[bool, str]:
    """Docker 守护进程是否可用（模块级缓存，避免每个任务都探一次）。"""
    if _DOCKER_PROBE["checked"]:
        return bool(_DOCKER_PROBE["ok"]), str(_DOCKER_PROBE["reason"])
    _DOCKER_PROBE["checked"] = True
    exe = shutil.which("docker")
    if not exe:
        _DOCKER_PROBE["reason"] = "未安装 docker"
        return False, _DOCKER_PROBE["reason"]
    try:
        proc = subprocess.run(
            [exe, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=probe_timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _DOCKER_PROBE["reason"] = f"docker 探测失败：{exc}"
        return False, _DOCKER_PROBE["reason"]
    if proc.returncode != 0:
        _DOCKER_PROBE["reason"] = (proc.stderr or proc.stdout or "docker 未运行").strip()[:200]
        return False, _DOCKER_PROBE["reason"]
    _DOCKER_PROBE["ok"] = True
    _DOCKER_PROBE["reason"] = (proc.stdout or "").strip()
    return True, _DOCKER_PROBE["reason"]


def _dangerous(command: str) -> str | None:
    for pattern, why in _DANGEROUS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            return why
    return None


def _response(output: str, exit_code: int, truncated: bool = False) -> ExecuteResponse:
    """原始输出 → ExecuteResponse：状态行与截断提示由 harness 统一追加。

    超出上限截断并置 truncated 标记，语义与框架 `LocalShellBackend` 一致。
    """
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        output = output.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    return ExecuteResponse(output=output or "<no output>", exit_code=exit_code, truncated=truncated)


def _combine(proc: subprocess.CompletedProcess) -> ExecuteResponse:
    """进程结果 → ExecuteResponse（stderr 逐行加 [stderr] 前缀，同框架约定）。"""
    parts: list[str] = []
    if proc.stdout:
        parts.append(proc.stdout)
    if proc.stderr:
        parts.extend(f"[stderr] {line}" for line in proc.stderr.strip().split("\n"))
    return _response("\n".join(parts), proc.returncode)


class ProBackend(WorkspaceBackend, SandboxBackendProtocol):
    """项目后端 + shell 执行（容器优先）。

    文件读写继承 deep 版的边界：只能摸活动项目与 `/.agent/skills/`，写成功
    登记产物。`execute` 的边界是另一种东西 —— 容器模式下只有项目目录被挂到
    `/work`，容器里 `cd ..` 也看不到宿主；宿主降级模式做不到这层隔离，所以
    结果里会带一句提示。
    """

    def __init__(
        self,
        root: str,
        ctx: AgentContext,
        *,
        mode: str = "auto",
        image: str = "python:3.12-slim",
        network: str = "bridge",
        timeout: int = 60,
        memory: str = "1g",
        cpus: str = "2",
    ) -> None:
        super().__init__(root, ctx)
        self.workspace_id = ctx.workspace_id
        self.requested_mode = (mode or "auto").lower()
        self.image = image or "python:3.12-slim"
        self.network = network or "bridge"
        self.default_timeout = max(5, min(int(timeout or 60), MAX_SHELL_TIMEOUT))
        self.memory = memory or "1g"
        self.cpus = cpus or "2"
        self._id = f"pro-{uuid.uuid4().hex[:8]}"
        self._host_shells: dict[Path, LocalShellBackend] = {}
        self.mode, self.mode_note = self._resolve_mode()

    @property
    def id(self) -> str:
        return self._id

    def _resolve_mode(self) -> tuple[str, str]:
        if self.requested_mode == "off":
            return "off", "沙箱已关闭"
        if self.requested_mode == "local":
            return "local", "宿主 shell（无隔离）"
        ok, note = docker_ready()
        if ok:
            return "container", note
        return "local", f"Docker 不可用（{note}），已降级宿主 shell"

    # ---- 工作目录 ----

    def _workdir(self) -> Path:
        """命令的工作目录 = 活动项目；没有项目时退回用户的临时沙盒。

        临时沙盒名与 code_run 的 `_scratch-<owner8>` 保持一致，两边的临时文件
        才落在同一个地方。
        """
        root = workspace_root(self.workspace_id)
        project = slugify(self.ctx.project or "")
        if project:
            return root / project
        tag = self.ctx.owner_id[:8] if self.ctx.owner_id else "anon"
        return root / f"_scratch-{tag}"

    def _shadow(self) -> str:
        """把宿主绝对路径换成模型视角的虚拟路径，避免泄漏服务器目录结构。"""
        try:
            root = workspace_root(self.workspace_id).resolve()
            rel = self._workdir().resolve().relative_to(root).as_posix()
            return "/" + rel if rel and rel != "." else "/"
        except (OSError, ValueError):
            return "/"

    # ---- shell 执行 ----

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        cmd = (command or "").strip()
        if not cmd:
            return _response("Error: command 不能为空。", 2)
        if self.mode == "off":
            return _response(
                "沙箱已关闭（APP_AGENT_PRO_SANDBOX=off），无法执行 shell 命令。"
                "请改用 read_file/write_file/code_run。",
                126,
            )
        if self.mode == "local":
            why = _dangerous(cmd)
            if why:
                return _response(
                    f"已拒绝执行：命令命中危险模式「{why}」。宿主 shell 没有隔离，"
                    "这类命令请在终端里自己执行。",
                    126,
                )

        workdir = self._workdir()
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _response(f"Error: 无法创建工作目录（{exc}）", 1)

        secs = self.default_timeout
        if timeout is not None:
            secs = max(1, min(int(timeout), MAX_SHELL_TIMEOUT))

        if self.mode == "container":
            return self._container_exec(cmd, workdir, secs)
        return self._host_exec(cmd, workdir, secs)

    def _container_exec(self, cmd: str, workdir: Path, secs: int) -> ExecuteResponse:
        """容器里执行一条 sh 脚本：脚本走 stdin（`sh -s`），不经二级引号解析。"""
        name = f"einstein-pro-{uuid.uuid4().hex[:8]}"
        argv = [
            shutil.which("docker") or "docker", "run", "--rm", "-i",
            "--name", name,
            "--network", self.network,
            "-v", f"{workdir}:/work",
            "-w", "/work",
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", "256",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "-e", "PYTHONIOENCODING=utf-8",
            "-e", "PYTHONUNBUFFERED=1",
            "-e", "HOME=/work",
            self.image, "sh", "-s",
        ]
        try:
            proc = subprocess.run(
                argv, check=False, capture_output=True, input=cmd,
                text=True, encoding="utf-8", errors="replace", timeout=secs,
            )
        except subprocess.TimeoutExpired:
            self._force_remove(name)  # docker 客户端被杀后容器还在跑，补一刀
            return _response(f"执行超时（{secs}s）已被终止。检查是否有死循环或等待输入。", 124)
        except OSError as exc:
            return _response(f"Error: 无法启动容器（{exc}）", 1)
        return _combine(proc)

    def _host_exec(self, cmd: str, workdir: Path, secs: int) -> ExecuteResponse:
        """宿主降级：执行交给框架的 LocalShellBackend（无隔离，但有 POSIX 语义）。

        模型写的是 POSIX 命令，而 Windows 的 `shell=True` 走 cmd.exe，所以有
        bash 时把命令落成临时脚本再执行，避开引号转义（命令内容不进命令行）。
        """
        shell = self._host_shell(workdir)
        tmpdir: Path | None = None
        try:
            if shutil.which("bash"):
                tmpdir = Path(tempfile.mkdtemp(prefix="einstein-sh-"))
                script = tmpdir / "run.sh"
                script.write_text(cmd, encoding="utf-8", newline="\n")
                res = shell.execute(f'bash "{script}"', timeout=secs)
            else:
                res = shell.execute(cmd, timeout=secs)
        finally:
            if tmpdir is not None:
                try:
                    (tmpdir / "run.sh").unlink(missing_ok=True)
                    tmpdir.rmdir()
                except OSError:
                    pass

        header = (
            f"[宿主 shell · 无隔离] cwd={self._shadow()} —— 这不是容器，"
            "命令可以访问工作区之外的文件。敏感操作请让用户确认。\n"
        )
        return ExecuteResponse(
            output=header + res.output, exit_code=res.exit_code, truncated=res.truncated
        )

    def _host_shell(self, workdir: Path) -> LocalShellBackend:
        shell = self._host_shells.get(workdir)
        if shell is None:
            shell = LocalShellBackend(
                root_dir=str(workdir),
                virtual_mode=False,
                timeout=self.default_timeout,
                max_output_bytes=MAX_OUTPUT_BYTES,
                env=_clean_env(),
            )
            self._host_shells[workdir] = shell
        return shell

    @staticmethod
    def _force_remove(name: str) -> None:
        try:
            subprocess.run(
                [shutil.which("docker") or "docker", "rm", "-f", name],
                capture_output=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


# ---------------------------------------------------------------- 浏览器工具


def _browser_tools(ctx: AgentContext, workspace_id: str) -> list[Any]:
    """三个浏览器工具。schema 由 `@tool` 从签名与 docstring 生成。"""

    @tool
    def browser_open(url: str, max_chars: int = 6000) -> str:
        """用浏览器打开网页，返回标题、正文和页面链接清单（一次抓全，省得反复请求）。

        Args:
            url: http(s) 地址。
            max_chars: 正文最多返回多少字符，默认 6000。
        """
        try:
            page = browser_pro.open_page(url, max_chars=max_chars)
        except Exception as exc:  # noqa: BLE001 — WebError/BrowserError 都转成人话
            return f"打开失败：{exc}"
        head = (
            f"# {page['title']}\n{page['final_url']}\n"
            f"（正文 {page['chars']} 字，链接 {page['links_total']} 个）\n\n"
        )
        links = "\n".join(f"- {r['text'][:60]} → {r['url']}" for r in page["links"][:20])
        body = page["text"] or "（该页面没有可读正文）"
        return head + body + ("\n\n## 页面链接\n" + links if links else "")

    @tool
    def browser_links(url: str, contains: str = "", limit: int = 40) -> str:
        """列出网页上的链接，可按关键词过滤，用于找导航或详情页地址。

        Args:
            url: http(s) 地址。
            contains: 只保留文字或链接里包含该关键词的条目。
            limit: 最多返回多少条，默认 40，上限 80。
        """
        try:
            data = browser_pro.links_of(url, contains=contains, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return f"解析链接失败：{exc}"
        if not data["links"]:
            return f"{data['final_url']} 没有匹配的链接（共匹配 {data['total']} 条）。"
        rows = "\n".join(f"- {r['text'][:60]} → {r['url']}" for r in data["links"])
        return f"{data['final_url']}（匹配 {data['total']} 条，列出前 {len(data['links'])} 条）\n{rows}"

    @tool
    def browser_shot(
        url: str,
        save_path: str = "shots/page.png",
        full_page: bool = True,
        width: int = 1280,
    ) -> str:
        """用无头浏览器截整页图并保存到项目里（截图会作为产物出现在界面上）。

        Args:
            url: http(s) 地址。
            save_path: 保存到项目内的相对路径，如 shots/home.png。
            full_page: 是否截整页；false 只截首屏。
            width: 视口宽度，默认 1280。
        """
        if not ctx.project:
            return "截图需要先有项目：请先 project_create 建项目，再让截图保存到项目里。"
        root = workspace_root(workspace_id) / slugify(ctx.project)
        try:
            target = sandbox.resolve_in(root, save_path)
        except sandbox.SandboxError as exc:
            return f"保存路径无效：{exc}"
        try:
            info = browser_pro.screenshot(url, target, full_page=full_page, width=width)
        except Exception as exc:  # noqa: BLE001
            return f"截图失败：{exc}"
        rel = target.relative_to(root).as_posix()
        ctx.note_artifact(rel, _artifact_kind(rel))
        kb = max(1, info["bytes"] // 1024)
        return (
            f"已截图：/{slugify(ctx.project)}/{rel}"
            f"（{info['title']}，{kb} KB，{'整页' if full_page else '首屏'}）"
        )

    return [browser_open, browser_links, browser_shot]


# ---------------------------------------------------------------- 提示词

PRO_EXTRA = """

【执行环境（Pro 版新增）】
1. 你有一个 `execute` 工具，可以在隔离环境里直接跑 shell 命令。命令按 POSIX sh 写（`ls`、`cat`、`grep`、`pip`、`python3`），工作目录就是当前项目的根目录；写文件默认就落在项目里。
2. 已经有专用工具的场景不要用 shell 绕：搜索内容用 `grep`、找文件用 `glob`、读写文件用 `read_file` / `write_file` / `edit_file`（它们更快、有边界、能登记产物）。`execute` 留给「装依赖、跑构建、批量命令、看真实输出」这类事。
3. 每次执行都有超时（默认 60 秒），长任务拆成小步；同一命令连续失败两次就换思路，不要原地重试。
4. 需要装包时直接用 `pip install`，容器会随任务销毁，装的东西不会留到下次。
5. 有三个浏览器工具：`browser_open`（标题+正文+链接）、`browser_links`（只抽链接，可按关键词过滤）、`browser_shot`（截整页图存进项目）。查资料优先 `browser_open`。

"""


def _build_pro_agent(
    ctx: AgentContext,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str,
    persona: str = "",
    temperature: float | None = None,
):
    settings = get_settings()
    backend = ProBackend(
        str(workspace_root(workspace_id)),
        ctx,
        mode=getattr(settings, "agent_pro_sandbox", "auto"),
        image=getattr(settings, "agent_pro_image", "python:3.12-slim"),
        network=getattr(settings, "agent_pro_network", "none"),
        timeout=getattr(settings, "agent_pro_shell_timeout", 60),
        memory=getattr(settings, "agent_pro_memory", "1g"),
        cpus=getattr(settings, "agent_pro_cpus", "2"),
    )
    agent = _build_agent(
        ctx, tool_ids, kb_ids, workspace_id,
        persona=persona,
        temperature=temperature,
        backend=backend,
        prompt_extra=PRO_EXTRA,
        extra_tools=_browser_tools(ctx, workspace_id),
        # 人工确认走框架 interrupt（与默认引擎同一套语义）
        interrupt_on=_approval_plan(ctx),
    )
    return agent, backend


def _mode_note(backend: ProBackend) -> dict:
    """运行前自述：降级到宿主 shell 时用户必须知道。"""
    shots = "" if browser_pro.capabilities()["screenshot"] else "；无可用无头浏览器，截图不可用"
    detail = f"（{backend.mode_note}）" if backend.mode_note else ""
    return {"kind": "note", "content": f"Pro 引擎已启用：沙箱 {backend.mode}{detail}{shots}"}


# ---------------------------------------------------------------- 运行入口


async def run_deep_pro_agent(
    task: str,
    tool_ids: list[str],
    kb_ids: list[str],
    workspace_id: str | None = None,
    ctx: AgentContext | None = None,
    history: list[dict] | None = None,
    persona: str = "",
    pinned_skills: list[str] | None = None,
    temperature: float | None = None,
    thread_id: str = "",
    resume: dict | None = None,
) -> AsyncIterator[dict]:
    """Pro 引擎入口：与 deep 版共用执行循环，多的是 execute / 浏览器。"""
    def build(c: AgentContext, ws: str) -> tuple[Any, list[dict]]:
        agent, backend = _build_pro_agent(
            c, tool_ids, kb_ids, ws, persona=persona, temperature=temperature
        )
        return agent, [_mode_note(backend)]

    async for event in _run_deep(
        task, tool_ids, kb_ids, workspace_id, ctx, history,
        build=build, metric_kind="agent_pro", pinned_skills=pinned_skills,
        thread_id=thread_id, resume=resume,
    ):
        yield event


__all__ = [
    "ProBackend",
    "docker_ready",
    "run_deep_pro_agent",
]
