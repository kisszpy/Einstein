"""代码执行能力：在项目目录内跑一小段脚本，把输出回给模型。

**三个后端**（`APP_AGENT_CODE_EXEC_SANDBOX` 选，`auto` 时按下面的顺序挑）：

| 后端 | 隔离 | 断网 | 适用 |
|---|---|---|---|
| `e2b` | 云端独立 VM | 由 `ALLOW_INTERNET` 决定 | 生产 / 多租户 / 要装包 |
| `container` | 本地 Docker，cap-drop + 只读根 | 断网（`--network none`） | 单机自托管 |
| `host` | 无隔离，仅工作目录限制 | 跟随宿主 | 显式开启才用，不建议 |

**这是安全边界，改之前先读完**：

- 没有隔离时**默认拒绝执行**。宿主进程里一段 `open("../.env")` 就能读到密钥，
  而网页抓来的内容又可能带着提示注入，两者一串就是服务器任意代码执行。
  要承担这个风险必须显式打开 `APP_AGENT_CODE_EXEC_ALLOW_HOST=true`。
- 云沙箱的出口默认**跟随配置**而不是服务端默认：沙箱里跑的是模型生成的代码，
  无条件放开出网等于给了一条外传通道。要装包时用 `..._E2B_ALLOW_OUT` 收窄到
  包仓库域名。

**多语言**：python / node / java / maven。语言归一、Maven 参数拼装、落盘命名、
错误提示都在 `sandbox_common`，三个后端共用一份，避免行为随部署方式漂移。
云沙箱与容器都靠模板/镜像预装运行时，容器断网时 Maven 用镜像里预热的
`/opt/m2` 离线仓库，见 `docker/code-sandbox.Dockerfile`。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.capabilities import e2b_sandbox, sandbox
from app.services.capabilities.sandbox_common import (
    DEFAULT_TIMEOUT,
    ESM_RE,
    JAVA_CLASS_RE,
    JVM_FLAGS,
    KEEP_ENV,
    LANG_ALIASES,
    LANG_LABEL,
    LANGUAGES,
    MAX_BUILD_TIMEOUT,
    MAX_OUTPUT,
    MAX_TIMEOUT,
    MAVEN_OPTS,
    CodeError,
    clean_env,
    clip,
    maven_args,
    normalize_lang,
    offline_hint,
    prepare_script,
)

logger = get_logger(__name__)

# 兼容旧引用：agent_deep_pro 与测试直接取这些名字。移动实现时留在原地做别名，
# 比改一圈调用方安全。
_clean_env = clean_env
_clip = clip
_maven_args = maven_args
_prepare_script = prepare_script
_offline_hint = offline_hint
_JVM_FLAGS = JVM_FLAGS
_ESM_RE = ESM_RE
_JAVA_CLASS_RE = JAVA_CLASS_RE
_KEEP_ENV = KEEP_ENV
_MAVEN_OPTS = MAVEN_OPTS
_LANG_LABEL = LANG_LABEL

# 每种语言的容器资源。JVM 那两档比脚本高：513m 下 `mvn package` 会被 OOM
# 杀掉，而 OOM 的表现是容器无输出、退出码 137，模型完全看不出是内存问题。
_SPECS = {
    "python": {"memory": "512m", "tmpfs": "256m", "pids": "128"},
    "node": {"memory": "512m", "tmpfs": "256m", "pids": "128"},
    "java": {"memory": "768m", "tmpfs": "256m", "pids": "256"},
    "maven": {"memory": "1g", "tmpfs": "512m", "pids": "512"},
}

# docker 探测结果缓存：每个任务探一次 docker version 就够，别每次都等 6 秒
_DOCKER: dict[str, object] = {"checked": False, "ok": False, "note": ""}

_BACKEND_LABEL = {
    "e2b": "云沙箱（Python / Node / Java / Maven）",
    "container": "容器沙箱（Python / Node / Java / Maven）",
    "host": "宿主进程（无隔离，已由配置显式开启）",
}


def docker_ready(probe_timeout: int = 6) -> tuple[bool, str]:
    """Docker 守护进程是否可用（模块级缓存）。返回 (ok, 说明)。"""
    if _DOCKER["checked"]:
        return bool(_DOCKER["ok"]), str(_DOCKER["note"])
    _DOCKER["checked"] = True
    exe = shutil.which("docker")
    if not exe:
        _DOCKER["note"] = "未安装 docker"
        return False, str(_DOCKER["note"])
    try:
        proc = subprocess.run(
            [exe, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=probe_timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _DOCKER["note"] = f"docker 探测失败：{exc}"
        return False, str(_DOCKER["note"])
    if proc.returncode != 0:
        _DOCKER["note"] = (proc.stderr or proc.stdout or "docker 未运行").strip()[:200]
        return False, str(_DOCKER["note"])
    _DOCKER["ok"] = True
    _DOCKER["note"] = (proc.stdout or "").strip()
    return True, str(_DOCKER["note"])


def resolve_mode() -> str:
    """本次该用哪种执行环境：e2b / container / host / unavailable。

    装配期（决定挂不挂 code_run 工具）与执行期共用它，两边结论必须一致，
    否则模型会看到一个"调了都说不行"的假工具。

    `auto` 的顺序是「配了云沙箱就走云」：凭证既然写进配置，就说明部署方想用它；
    否则会出现「配了 Key 却毫无变化」——这正是我们之前踩过的坑。
    """
    settings = get_settings()
    raw = (getattr(settings, "agent_code_exec_sandbox", "auto") or "auto").strip().lower()
    if raw in ("e2b", "cloud", "agent-sandbox", "aliyun", "sandbox"):
        return "e2b" if e2b_sandbox.available()[0] else "unavailable"
    if raw in ("container", "docker"):
        return "container" if docker_ready()[0] else "unavailable"
    if raw in ("host", "local"):
        return "host"
    if e2b_sandbox.available()[0]:
        return "e2b"
    if docker_ready()[0]:
        return "container"
    return "host" if getattr(settings, "agent_code_exec_allow_host", False) else "unavailable"


def available() -> tuple[bool, str]:
    """工具装配用：能不能跑代码，不能的话原因是什么（写进日志给人看）。"""
    mode = resolve_mode()
    if mode in _BACKEND_LABEL:
        return True, _BACKEND_LABEL[mode]
    # 两个原因都写出来：只说其中一个会把排查引到错的方向
    return False, (
        f"没有可用的代码沙箱（云沙箱：{e2b_sandbox.available()[1]}；"
        f"本地容器：{docker_ready()[1]}），且宿主执行未开启"
        "（APP_AGENT_CODE_EXEC_ALLOW_HOST）"
    )


def capability_note() -> str:
    """给工具描述用的能力说明。

    模型对「能不能装包」的判断直接决定它会不会白试一轮：容器里写
    `pip install seaborn`，等 30 秒超时，再改代码——这一轮本来是可以在描述里
    避免的。
    """
    mode = resolve_mode()
    if mode == "e2b":
        net = "沙箱可出网，缺包可直接 pip/npm install" if e2b_sandbox.allow_internet() \
            else "沙箱断网，只能用手头依赖"
        # 云端模板带 JDK 21 但没有 mvn，是「首次调用时自动装」而不是「不支持」。
        # 不说清楚，模型会先试一次 command not found 再放弃，白跑一轮。
        return f"{net}；另有 JDK 21，maven 首次调用自动安装后可用"
    if mode == "container":
        return "沙箱断网：pip/npm 装不了包，Maven 走镜像内置的离线仓库"
    return "宿主进程执行，直接用本机环境"


def needs_approval() -> bool:
    """执行代码这一步要不要人工确认。

    **按执行环境判定，而不是按工具名**。这个区分的依据是「代码能不能越界」：
    云沙箱与容器的文件系统是独立的，服务端密钥根本不在里面，跑任意代码最多
    是把自己的沙箱弄脏；宿主进程则不然，`open("../.env")` 就能读到密钥。

    所以：

    - `host` → 每次都问（唯一真正需要把门的地方）
    - `e2b` / `container` → 默认不问，除非 `APP_AGENT_APPROVAL_ISOLATED=true`
      把旧行为要回来
    - `unavailable` → 问。此时工具本来也不挂，走到这里说明配置被改过，
      保守一点没有坏处

    **别在这里读「用户刚点过什么」**。这个函数会被框架在中断与续跑时各求值
    一次（见 `InterruptOnConfig.when` 的约定），两次结论不同会让已作出的
    审批决定对不上号，拒绝可能被执行。环境在一次运行内是稳定的，审批记录不是。
    """
    mode = resolve_mode()
    if mode == "host":
        return True
    if mode not in _BACKEND_LABEL:
        return True
    return bool(getattr(get_settings(), "agent_approval_isolated", False))


def _container_docker() -> str:
    return shutil.which("docker") or "docker"


def _force_remove(name: str) -> None:
    """docker 客户端被超时杀掉后容器还在跑，补一刀。"""
    try:
        subprocess.run(
            [_container_docker(), "rm", "-f", name],
            capture_output=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # 清理失败不影响主流程


def _winpath(p: Path | str) -> str:
    """Windows 反斜杠会被 docker CLI 的 `宿主:容器` 分隔符切错，统一正斜杠。"""
    return str(p).replace(chr(92), "/")


def _inner_path(p: Path, root: Path) -> str:
    """把宿主路径折算成容器内路径（/work 是项目根的挂载点）。"""
    try:
        rel = p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "/work/.agent_tmp/" + p.name
    return "/work" if rel in ("", ".") else "/work/" + rel


def _run_in_container(
    script: Path | None,
    root: Path,
    lang: str,
    secs: int,
    code: str = "",
) -> dict:
    """在一次性容器里跑：只挂项目目录、断网、只读根、cap-drop ALL。"""
    settings = get_settings()
    image = getattr(settings, "agent_code_exec_image", "python:3.12-slim") or "python:3.12-slim"
    network = getattr(settings, "agent_code_exec_network", "none") or "none"
    spec = _SPECS[lang]
    name = f"einstein-code-{uuid.uuid4().hex[:8]}"

    inner = _inner_path(script, root) if script else "/work"
    script_label = inner.replace("/work/", "", 1) if inner != "/work" else "."

    env_args = [
        "-e", "PYTHONIOENCODING=utf-8",
        "-e", "PYTHONUNBUFFERED=1",
        # HOME 指到 tmpfs：留在 /work 会让 matplotlib 之类在用户项目里拉出
        # .cache/.config 一堆垃圾目录，用户看到的是"我的项目被污染了"
        "-e", "HOME=/tmp",
        "-e", "TZ=Asia/Shanghai",
        "-e", "NO_COLOR=1",
    ]

    argv = [
        _container_docker(), "run", "--rm",
        "--name", name,
        "--network", network,
        "-v", f"{_winpath(root)}:/work",
        "-w", "/work",
        "--memory", spec["memory"],
        "--cpus", "1",
        "--pids-limit", spec["pids"],
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--read-only",
        "--tmpfs", f"/tmp:rw,exec,nosuid,size={spec['tmpfs']}",
    ]

    # 宿主 ~/.m2 只读挂载：补齐镜像没预热的依赖。settings 放在 /opt/mvn-settings.xml
    # 而不是 /opt/m2 里面，就是为了不被这次挂载覆盖掉。
    m2_host = (getattr(settings, "agent_code_exec_m2_host", "") or "").strip()
    if lang in ("java", "maven") and m2_host and Path(m2_host).expanduser().is_dir():
        argv += ["-v", f"{_winpath(Path(m2_host).expanduser())}:/opt/m2:ro"]

    # 容器里工作目录固定 /work，pom 目录用 -f 指定即可，不依赖 cwd
    pom_dir = ""
    if lang == "maven" and script is not None:
        pom_dir = _inner_path(script.parent if script.is_file() else script, root)

    tail = run_tail(lang, inner, code, pom_dir=pom_dir)
    if lang == "maven":
        env_args += ["-e", f"MAVEN_OPTS={_MAVEN_OPTS}"]
    argv += env_args + [image, *tail]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=secs,
        )
    except subprocess.TimeoutExpired:
        _force_remove(name)
        return {
            "language": lang,
            "script": script_label,
            "exit_code": -1,
            "ok": False,
            "stdout": "",
            "stderr": f"执行超时（{secs}s）已被终止。检查是否有死循环或等待输入。",
            "output_truncated": False,
            "sandbox": "container",
        }
    except OSError as exc:
        raise CodeError(f"无法启动容器（{exc}）") from exc

    # hint 要在裁剪之前判断：错误信息被截断后特征词可能正好落在被删掉的那半
    hint = _offline_hint(lang, (proc.stderr or "") + (proc.stdout or ""))
    out, cut_out = _clip(proc.stdout or "")
    err, cut_err = _clip(proc.stderr or "")
    if proc.returncode != 0 and hint:
        err += hint
    return {
        "language": lang,
        "script": script_label,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": out,
        "stderr": err,
        "output_truncated": cut_out or cut_err,
        "sandbox": "container",
    }


def run_tail(lang: str, inner: str, code: str, pom_dir: str = "") -> list[str]:
    """容器里的启动命令。


    和 `sandbox_common.run_command` 的区别只有 Maven：容器用 `-f` 指 pom，
    云沙箱用 cwd 指 pom。拆开写是因为两边的工作目录模型不同，硬统一反而更容易错。
    """
    if lang == "python":
        return ["python", "-X", "utf8", inner]
    if lang == "node":
        # 项目里已有的 .ts 直接交给 tsx（镜像预装），别逼模型先编译一遍
        if inner.endswith((".ts", ".mts", ".cts")):
            return ["tsx", inner]
        return ["node", "--no-warnings", inner]
    if lang == "java":
        return ["java", *_JVM_FLAGS, inner]
    return ["mvn", *_maven_args(code, pom_dir)]


def _run_on_host(cmd: list[str], root: Path, secs: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=secs,
        env=_clean_env(),
    )


def _host_cmd(lang: str, script: Path, code: str) -> list[str]:
    if lang == "python":
        return [sys.executable, "-X", "utf8", str(script)]
    if lang == "node":
        node = shutil.which("node")
        if not node:
            raise CodeError("环境中没有 node，无法执行 JavaScript")
        return [node, str(script)]
    if lang == "java":
        java = shutil.which("java")
        if not java:
            raise CodeError("环境变量 PATH 里没有 java，无法执行 Java")
        return [java, *_JVM_FLAGS, str(script)]

    mvn = shutil.which("mvn")
    if not mvn:
        raise CodeError("环境变量 PATH 里没有 mvn，无法执行 Maven")
    # 宿主模式没有容器里的 /opt/mvn-settings.xml，也就不加 -s（用本机 Maven 自己的配置）
    args = _maven_args(code, "", settings_path="")
    pom = script if script.name == "pom.xml" else script / "pom.xml"
    if not any(a in ("-f", "--file") or a.startswith(("-f=", "--file=")) for a in args):
        args += ["-f", str(pom)]
    return [mvn, *args]


# 产物快照要跳过的目录：与上传/回写同一套口径。`.agent_tmp` 里是本次的临时
# 脚本，把它算成产物会让每轮都多出一张「gen_ab12.py」的卡片。
_SNAP_SKIP = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".agent_tmp", "target", ".gradle", ".idea", ".vscode",
}


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """项目目录的 (大小, mtime) 快照，用来判断「这次跑完多了/改了哪些文件」。

    云沙箱能直接拿到回写清单，容器与宿主模式的文件是直接落盘的——没有清单
    就只能靠前后对比。三种模式统一走这一条，前端才会在每种部署下都收到产物。
    """
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SNAP_SKIP]
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            try:
                st = p.stat()
            except OSError:
                continue
            snap[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return snap


def _produced(root: Path, before: dict[str, tuple[int, int]], limit: int = 200) -> list[str]:
    after = _snapshot(root)
    rows = [
        rel for rel, sig in after.items()
        if before.get(rel) != sig and not rel.startswith(".agent_tmp/")
    ]
    return sorted(rows)[:limit]


def run_code(
    workspace_id: str,
    project: str,
    code: str = "",
    path: str = "",
    language: str = "python",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    root = sandbox.project_dir(workspace_id, project)
    lang = normalize_lang(language)

    mode = resolve_mode()
    if mode == "unavailable":
        raise CodeError(
            "当前环境没有可用的代码执行沙箱"
            f"（{e2b_sandbox.available()[1]}；{docker_ready()[1]}）。"
            "为避免在宿主进程里直接运行生成代码，本次执行已被拒绝。"
            "请让部署方配置云沙箱、启动 Docker，或显式设置 "
            "APP_AGENT_CODE_EXEC_ALLOW_HOST=true。"
        )

    cap = MAX_BUILD_TIMEOUT if lang in ("java", "maven") else MAX_TIMEOUT
    secs = max(5, min(int(timeout or DEFAULT_TIMEOUT), cap))
    tmp_dir = root / ".agent_tmp"
    tmp_dir.mkdir(exist_ok=True)

    if path:
        target = sandbox.resolve_in(root, path)
        # maven 的 path 可以给项目目录（含 pom.xml）或 pom.xml 本身
        if target.is_file() or (lang == "maven" and target.is_dir()):
            script: Path | None = target
            cleanup = False
        else:
            raise CodeError(f"文件不存在：{path}")
    else:
        if not (code or "").strip():
            raise CodeError("code 与 path 至少要给一个")
        if lang == "maven":
            raise CodeError(
                "language=maven 需要在项目里有 pom.xml：先用 fs_write 写 pom.xml 与 src/，"
                "再用 path 指向该目录（或直接省略 path 用项目根），code 里给 mvn 参数（如 package）"
            )
        script, cleanup = _prepare_script(lang, code, tmp_dir)

    if path:
        label = path
    elif script is not None:
        label = f".agent_tmp/{script.name}"
    else:
        label = "."

    # 执行前的项目快照：跑完和它比一比，就知道本次新增/改动了哪些文件。
    # 这些文件既是「交付物」，也是前端产物卡片的来源。
    before = _snapshot(root)

    def _done(result: dict) -> dict:
        # 云沙箱自带准确的回写清单；容器/宿主没有，靠快照差异补
        if not result.get("files"):
            result["files"] = _produced(root, before)
        return result

    try:
        if mode == "e2b":
            try:
                return _done(e2b_sandbox.run(root, script, lang, secs, code, label, workspace_id, project))
            except CodeError as exc:
                # 云端故障不该让模型以为是自己代码写错了：有容器就降级重跑一遍，
                # 并把原因写在 stderr 开头（模型看得到，不会重复踩）。
                if not docker_ready()[0]:
                    raise
                logger.warning("云沙箱不可用，降级容器执行：%s", exc)
                res = _run_in_container(script, root, lang, secs, code)
                res["stderr"] = f"[云沙箱不可用，本次改在本地容器执行：{exc}]\n{res['stderr']}"
                return _done(res)

        if mode == "container":
            return _done(_run_in_container(script, root, lang, secs, code))

        # 宿主模式：调用方已通过配置显式承担了「无隔离」的风险
        assert script is not None
        cmd = _host_cmd(lang, script, code)
        try:
            proc = _run_on_host(cmd, root, secs)
        except subprocess.TimeoutExpired:
            return _done({
                "language": lang,
                "script": label,
                "exit_code": -1,
                "ok": False,
                "stdout": "",
                "stderr": f"执行超时（{secs}s）已被终止。检查是否有死循环或等待输入。",
                "output_truncated": False,
                "sandbox": "host",
            })
        out, cut_out = _clip(proc.stdout or "")
        err, cut_err = _clip(proc.stderr or "")
        hint = _offline_hint(lang, (proc.stderr or "") + (proc.stdout or ""))
        if proc.returncode != 0 and hint:
            err += hint
        return _done({
            "language": lang,
            "script": label,
            "exit_code": proc.returncode,
            "ok": proc.returncode == 0,
            "stdout": out,
            "stderr": err,
            "output_truncated": cut_out or cut_err,
            "sandbox": "host",
        })
    finally:
        if cleanup:
            try:
                script.unlink(missing_ok=True)  # type: ignore[union-attr]
            except OSError:
                pass  # 清理失败不影响主流程（Windows 上文件常被占用）
