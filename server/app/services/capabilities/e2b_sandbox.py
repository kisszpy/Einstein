"""云沙箱后端：阿里云 Agent Sandbox（兼容 E2B 协议）。

**为什么要有它**：Docker 后端要求部署机跑 Docker、分发 1.5GB 镜像，而且容器
必须断网——模型想装个包就得让部署方重建镜像。云沙箱换成按秒计费的托管 VM：
VM 级隔离、模板预装运行时、出口可开白名单。代价是项目文件要上行，每次调用
多几十到几百毫秒的网络往返。

**三条硬规则**：

1. **基础设施故障必须可回落**（raise `CodeError`）：建会话失败、鉴权失败这类
   问题不该变成「模型写的代码有问题」，由 `code_exec` 捕获后降级到容器。
   代码本身非零退出则**不算故障**，正常回 dict。
2. **会话按项目复用**：同一个 (workspace, project) 复用同一个沙箱，只上传
   变化的文件；空闲超过 `idle` 秒回收，避免空转计费。
3. **出网由配置决定，不跟随服务端默认**。要装包时用 `allow_out` 把范围收在
   包仓库上，而不是无条件放开——沙箱里跑的是模型生成的代码，加上来自网页的
   提示注入，等于给了一条外传通道。

文件同步的边界（写在这免得以后被当 bug 修）：
- 只同步项目目录内的普通文件，跳过 `.git` / `node_modules` / `.venv` 这类
  依赖与缓存目录；
- 单文件 8MB、总量 64MB 上限，超了跳过并在结果里留痕；
- 回写只取「本次命令改过」的文件（按 mtime 与 size 比对），不做全量拉取。
"""

from __future__ import annotations

import atexit
import os
import shlex
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.capabilities.sandbox_common import (
    MAVEN_OPTS,
    CodeError,
    clip,
    offline_hint,
    run_command,
)

logger = get_logger(__name__)

# 依赖 / 缓存目录不必上传：占了体积又不会被模型读，`node_modules` 动辄几百 MB
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".agent_tmp", "target", ".gradle", ".idea", ".vscode",
}
_SKIP_SUFFIX = (".pyc", ".pyo")

# 构建中间产物不必回写：`mvn package` 会产出几十个 .class，全拉回来既慢又只是
# 噪音，下次构建还会被覆盖。
#
# 注意这里**不能**放 `out` / `dist` / `build`：它们名字像构建目录，实际是很多
# 项目的**交付目录**（前端打包产物、导出报告、图表）。之前把它们按「构建垃圾」
# 过滤掉，结果就是模型在沙箱里生成的 PPT/网页根本没回来——用户看到的是
# 「交付物不在本地」。体积靠 MAX_WRITE_BACK_FILES 与字节预算兜底，不靠目录名猜。
_NO_WRITE_BACK = {
    "target", "node_modules", ".git", "__pycache__",
    ".venv", "venv", ".gradle", ".m2", ".agent_tmp",
}

MAX_UPLOAD_FILES = 3000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
# 一次命令最多回写多少个文件：构建目录放开之后，`npm run build` 可能产出几百个
# 分块文件，全量拉回会让一次 code_run 卡上十几秒。超出按路径序截断并告知模型。
MAX_WRITE_BACK_FILES = 200
_LIST_DEPTH = 12


@dataclass
class _Session:
    box: Any                     # e2b.Sandbox
    workdir: str
    last_used: float
    # rel -> [size, 上传完成时刻]。回写时用它区分「我们刚放上去的」与「命令改过的」
    uploaded: dict[str, list[float]] = field(default_factory=dict)
    # 工具链状态："" 未探测 / "ready" 可用 / "missing:…" 不可用（含原因）。
    # 官方模板没有 mvn，第一次跑 maven 时才装，装完记住，别每次调用都探一遍。
    maven: str = ""


_SESSIONS: dict[str, _Session] = {}
_CREATING: dict[str, threading.Lock] = {}
_LOCK = threading.RLock()
_REAPER: threading.Thread | None = None


# ------------------------------------------------------------------ 配置

def _s(name: str, default: Any = "") -> Any:
    return getattr(get_settings(), name, default)


def api_key() -> str:
    return str(_s("agent_code_exec_e2b_api_key", "") or "").strip()


def configured() -> bool:
    return bool(api_key())


def available() -> tuple[bool, str]:
    """装配期判断：能不能走云沙箱。只做本地检查，不发网络请求。

    这里刻意不探活：工具装配发生在每次 Agent 启动时，加一次 HTTP 往返会把
    「点一下发送」变成「先等两秒」。真正的连通性在第一次执行时暴露，失败会
    回落容器。
    """
    if not configured():
        return False, "未配置 APP_AGENT_CODE_EXEC_E2B_API_KEY"
    try:
        import e2b  # noqa: F401
    except ImportError:
        return False, "未安装 e2b SDK（pip install e2b==2.31.0）"
    return True, f"云沙箱（模板 {template()}）"


def template() -> str:
    return str(_s("agent_code_exec_e2b_template", "code-interpreter-v1") or "code-interpreter-v1")


def _workdir() -> str:
    return str(_s("agent_code_exec_e2b_workdir", "/home/user/work") or "/home/user/work")


def _allow_internet() -> bool:
    return bool(_s("agent_code_exec_e2b_allow_internet", True))


def allow_internet() -> bool:
    """给工具描述用：模型需要知道「能不能自己装包」，否则会先试一次再放弃。"""
    return _allow_internet()


def _allow_out() -> list[str]:
    raw = str(_s("agent_code_exec_e2b_allow_out", "") or "")
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def _maven_url() -> str:
    return str(_s("agent_code_exec_e2b_maven_url", "") or "").strip()


def _maven_mirror() -> str:
    return str(_s("agent_code_exec_e2b_maven_mirror", "") or "").strip()


# ------------------------------------------------------------------ 会话

def _key(workspace_id: str, project: str) -> str:
    return f"{workspace_id}/{project}"


def _create_box(depth_hint: str = "") -> Any:
    """新建一个沙箱。鉴权/配额/模板问题都在这里暴露成 CodeError。"""
    from e2b import Sandbox
    from e2b.exceptions import AuthenticationException, SandboxException

    kwargs: dict[str, Any] = {
        "template": template(),
        "timeout": int(_s("agent_code_exec_e2b_lifetime", 900) or 900),
        "api_key": api_key(),
        "allow_internet_access": _allow_internet(),
        "envs": {
            "HOME": "/home/user",
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
        },
    }
    api_url = str(_s("agent_code_exec_e2b_api_url", "") or "").strip()
    domain = str(_s("agent_code_exec_e2b_domain", "") or "").strip()
    if api_url:
        kwargs["api_url"] = api_url
    if domain:
        kwargs["domain"] = domain
    allow_out = _allow_out()
    if allow_out:
        kwargs["network"] = {"allow_out": allow_out, "deny_out": ["0.0.0.0/0"]}

    try:
        box = Sandbox.create(**kwargs)
    except AuthenticationException as exc:
        raise CodeError(f"云沙箱鉴权失败（检查 API Key 与地域是否匹配）：{exc}") from exc
    except SandboxException as exc:
        raise CodeError(f"云沙箱创建失败（模板 {template()}）：{exc}") from exc
    workdir = _workdir()
    try:
        box.commands.run(f"mkdir -p {shlex.quote(workdir)}", timeout=30)
    except Exception as exc:  # noqa: BLE001 - 建目录失败说明会话不可用，直接回落
        raise CodeError(f"云沙箱不可用（建工作目录失败）：{exc}") from exc
    logger.info("云沙箱已创建：%s 模板=%s %s", getattr(box, "sandbox_id", "?"), template(), depth_hint)
    return box


def _session(workspace_id: str, project: str, *, fresh: bool = False) -> _Session:
    """取（或建）该项目的沙箱。

    刻意不在每次调用时探活：`is_running()` 是一次网络往返，而「会话还能不能
    用」在真正执行时一定会暴露，届时 `_drop` 掉重建即可。用探活换 100ms 延迟
    不划算。

    建会话要花几秒，所以不能全程持全局锁——否则两个项目同时跑代码会互相排队。
    每个 key 一把小锁，保证同一个项目不会被并发建出两个沙箱。
    """
    key = _key(workspace_id, project)
    with _LOCK:
        _reap()
        sess = _SESSIONS.get(key) if not fresh else None
        if sess is not None:
            sess.last_used = time.time()
            return sess
        create_lock = _CREATING.setdefault(key, threading.Lock())

    with create_lock:
        with _LOCK:  # 拿锁期间别的线程可能刚建好
            sess = _SESSIONS.get(key)
            if sess is not None:
                sess.last_used = time.time()
                return sess
        box = _create_box(depth_hint=key)
        sess = _Session(box=box, workdir=_workdir(), last_used=time.time())
        with _LOCK:
            _SESSIONS[key] = sess
        _start_reaper()
        return sess


def _drop(key: str, *, kill: bool = False) -> None:
    sess = _SESSIONS.pop(key, None)
    if sess is None or not kill:
        return
    try:
        sess.box.kill()
    except Exception:  # noqa: BLE001 - 回收失败不影响主流程，到期自会释放
        pass


def _reap() -> None:
    """回收空闲会话。每次取会话时顺手跑一遍，不需要独立定时器。"""
    idle = float(_s("agent_code_exec_e2b_idle", 300) or 300)
    now = time.time()
    for key in [k for k, v in _SESSIONS.items() if now - v.last_used > idle]:
        logger.info("回收空闲云沙箱：%s（空闲 %.0fs）", key, now - _SESSIONS[key].last_used)
        _drop(key, kill=True)


def _start_reaper() -> None:
    global _REAPER
    if _REAPER is not None and _REAPER.is_alive():
        return
    _REAPER = threading.Thread(target=_reaper_loop, name="e2b-reaper", daemon=True)
    _REAPER.start()


def _reaper_loop() -> None:
    while True:
        time.sleep(30)
        try:
            with _LOCK:
                _reap()
        except Exception:  # noqa: BLE001 - 守护线程不能死
            logger.exception("云沙箱回收线程异常")


def close_all() -> None:
    with _LOCK:
        for key in list(_SESSIONS):
            _drop(key, kill=True)


atexit.register(close_all)


# ------------------------------------------------------------- 文件同步

def _iter_local(root: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    """列出要上传的文件，返回 (文件列表, 跳过说明)。"""
    files: list[tuple[str, Path]] = []
    skipped: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(_SKIP_SUFFIX):
                continue
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(f"{name}（超过 8MB）")
                continue
            if len(files) >= MAX_UPLOAD_FILES or total + size > MAX_TOTAL_BYTES:
                skipped.append("项目文件过多或过大，剩余未上传")
                return files, skipped
            total += size
            files.append((path.relative_to(root).as_posix(), path))
    return files, skipped


def _inner(workdir: str, rel: str) -> str:
    """项目相对路径 → 沙箱内绝对路径。`.` 表示项目根，直接落回工作目录。"""
    if rel in ("", "."):
        return workdir
    return f"{workdir}/{rel[2:] if rel.startswith('./') else rel}"


def _mkdirs(box: Any, workdir: str, rels: list[str]) -> None:
    dirs = sorted({os.path.dirname(_inner(workdir, r)) for r in rels} - {workdir, ""})
    for i in range(0, len(dirs), 40):
        chunk = dirs[i:i + 40]
        box.commands.run("mkdir -p " + " ".join(shlex.quote(d) for d in chunk), timeout=60)


def _sync_up(sess: _Session, root: Path, extra: Path | None = None) -> list[str]:
    """增量上传。返回跳过说明（写进结果，别让「文件没上去」变成静默 bug）。"""
    files, skipped = _iter_local(root)
    # extra 只接受单个文件：Maven 的 script 可能是**目录**（pom 所在目录），
    # 传目录进来会在 read_bytes() 上炸掉，而那是最常见的 maven 调用方式。
    if extra is not None and extra.is_file():
        try:
            rel = extra.relative_to(root).as_posix()
        except ValueError:
            rel = ""
        if rel and rel not in {r for r, _ in files}:
            files.append((rel, extra))

    pending = []
    for rel, path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        known = sess.uploaded.get(rel)
        if known and int(known[0]) == st.st_size and st.st_size > 0:
            continue  # 大小没变就当没改过：省一次往返，代价是「同尺寸改写」不重传
        pending.append((rel, path, st.st_size))
    if not pending:
        return skipped

    _mkdirs(sess.box, sess.workdir, [r for r, _, _ in pending])
    for rel, path, size in pending:
        try:
            sess.box.files.write(_inner(sess.workdir, rel), path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不该毁掉整次执行
            raise CodeError(f"向云沙箱上传 {rel} 失败：{exc}") from exc
        sess.uploaded[rel] = [float(size), time.time()]
    return skipped


def _is_regular_file(entry: Any) -> bool:
    """判断列表项是不是普通文件。

    envd 返回 `FileType.FILE` 这样的枚举，`str()` 出来是 "FileType.FILE"；
    也有版本给纯字符串 "file"。两种都认，避免因 SDK 版本差异静默失效。
    """
    raw = str(getattr(entry, "type", "") or "").lower()
    if "dir" in raw:
        return False
    return raw.endswith("file")


def _no_write_back(rel: str) -> bool:
    return any(part in _NO_WRITE_BACK for part in rel.split("/")[:-1])


def _sync_down(sess: _Session, root: Path) -> tuple[list[str], list[str]]:
    """只取「本次命令改过」的文件。

    返回 `(已回写, 因体积/数量被跳过)`。跳过的必须回报给模型：静默丢弃会让它
    以为文件交付成功，用户拿到的却是一个空项目。
    """
    written: list[str] = []
    skipped: list[str] = []
    try:
        entries = sess.box.files.list(sess.workdir, depth=_LIST_DEPTH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("云沙箱文件列表读取失败，跳过回写：%s", exc)
        return written, skipped

    prefix = sess.workdir.rstrip("/") + "/"
    total = 0
    for entry in entries:
        # envd 的 type 是枚举（`FileType.FILE`），不是字符串 `"file"`：
        # 直接和 "file" 比较永远不等，回写会静默地一条都不做。
        if not _is_regular_file(entry):
            continue
        path = str(getattr(entry, "path", "") or "")
        # envd 返回的 path 通常是绝对路径；个别版本只给相对名，兜一层
        rel = path[len(prefix):] if path.startswith(prefix) else (path.lstrip("/") or str(getattr(entry, "name", "")))
        if not rel or rel.startswith(".agent_tmp/") or _no_write_back(rel):
            continue

        size = int(getattr(entry, "size", 0) or 0)
        if size > MAX_FILE_BYTES or total + size > MAX_DOWNLOAD_BYTES:
            skipped.append(rel)
            continue
        known = sess.uploaded.get(rel)
        mtime = getattr(entry, "modified_time", None)
        mtime_ts = mtime.timestamp() if hasattr(mtime, "timestamp") else 0.0
        changed = (
            known is None
            or int(known[0]) != size
            or (mtime_ts and mtime_ts > float(known[1]) + 0.5)
        )
        if not changed:
            continue
        if len(written) >= MAX_WRITE_BACK_FILES:
            skipped.append(rel)
            continue
        try:
            data = sess.box.files.read(path or _inner(sess.workdir, rel), format="bytes")
        except Exception as exc:  # noqa: BLE001
            logger.warning("回写 %s 失败：%s", rel, exc)
            skipped.append(rel)
            continue
        # format="bytes" 实际返回 bytearray，不是 bytes：用 isinstance(x, bytes)
        # 判断会把二进制内容转成 "bytearray(b'…')" 这串字面量写进用户文件。
        if isinstance(data, (bytes, bytearray, memoryview)):
            blob = bytes(data)
        else:
            blob = str(data).encode()
        target = (root / rel)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        except OSError as exc:
            logger.warning("本地写入 %s 失败：%s", rel, exc)
            skipped.append(rel)
            continue
        sess.uploaded[rel] = [float(len(blob)), time.time()]
        total += len(blob)
        written.append(rel)
    return written, skipped


# ------------------------------------------------------------- 工具链

MVN_HOME = "/opt/maven"
MVN_BIN = f"{MVN_HOME}/bin/mvn"

# 只镜像 central：插件的默认仓库 id 也是 central，一个 mirror 同时覆盖依赖与插件；
# 写成 `*` 会把项目自己配的私有仓库也劫持掉。
_SETTINGS_XML = """<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <mirrors>
    <mirror>
      <id>aliyun</id>
      <name>aliyun public</name>
      <url>{url}</url>
      <mirrorOf>central</mirrorOf>
    </mirror>
  </mirrors>
</settings>
"""

# 一次装好「二进制 + 镜像配置」，两步都幂等，重复跑只是几条 stat。
# 装到 /opt 而不是 $HOME：/opt 与沙箱同寿命，HOME 有可能被会话重置。
_BOOTSTRAP = """set -e
MVN_BIN={mvn}
if [ ! -x "$MVN_BIN" ]; then
  if command -v mvn >/dev/null 2>&1; then
    MVN_BIN=$(command -v mvn)
  else
    if [ -z "{url}" ]; then echo "__MISSING__ 未配置 APP_AGENT_CODE_EXEC_E2B_MAVEN_URL"; exit 3; fi
    echo "__INSTALL__ 模板没有 Maven，正在安装（一次性，约 10MB）" >&2
    curl -fsSL --retry 2 --max-time 120 -o /tmp/apache-maven.tgz "{url}"
    sudo rm -rf {home}
    sudo mkdir -p {home}
    sudo tar xzf /tmp/apache-maven.tgz -C {home} --strip-components=1
    rm -f /tmp/apache-maven.tgz
    sudo ln -sf "$MVN_BIN" /usr/local/bin/mvn 2>/dev/null || true
  fi
fi
mkdir -p "$HOME/.m2"
cat > "$HOME/.m2/settings.xml" <<'__XML__'
{settings}
__XML__
echo "__MVN__ $MVN_BIN"
"""


def _ensure_maven(sess: _Session) -> str:
    """保证会话里有可用的 mvn，返回二进制路径。失败直接抛 CodeError。

    官方 `code-interpreter-v1` 带 JDK 21 却没有 Maven，而 Maven 构建是我们
    明确支持的四种语言之一。这里按「已有 → 系统里有 → 现装」三级取，装一次
    记在会话上，复用的会话不会再付这笔钱。
    """
    if sess.maven == "ready":
        return MVN_BIN
    if sess.maven.startswith("missing:"):
        raise CodeError(f"云沙箱里没有 Maven：{sess.maven[8:]}")

    settings = _SETTINGS_XML.format(url=_maven_mirror()) if _maven_mirror() else "<settings/>"
    script = _BOOTSTRAP.format(
        mvn=MVN_BIN, home=MVN_HOME, url=_maven_url(), settings=settings.strip()
    )
    started = time.time()
    try:
        res = sess.box.commands.run(script, timeout=180, request_timeout=210)
        out = res.stdout or ""
    except Exception as exc:  # noqa: BLE001 - 装不上就是「这台跑不了 maven」
        sess.maven = f"missing:{exc}"
        raise CodeError(f"云沙箱安装 Maven 失败：{exc}") from exc

    mvn_bin = MVN_BIN
    for line in out.splitlines():
        if line.startswith("__MVN__ "):
            mvn_bin = line[8:].strip()
        elif line.startswith("__MISSING__ "):
            sess.maven = "missing:" + line[12:].strip()
            raise CodeError(sess.maven[8:])

    sess.maven = "ready"
    logger.info(
        "云沙箱 Maven 就绪：%s（耗时 %.1fs）", mvn_bin, time.time() - started
    )
    return mvn_bin


# ------------------------------------------------------------------ 执行


def run(
    root: Path,
    script: Path | None,
    lang: str,
    secs: int,
    code: str,
    path_label: str,
    workspace_id: str,
    project: str,
) -> dict:
    """在云沙箱里跑一次。基础设施故障抛 CodeError（由上层回落容器）。"""
    from e2b.exceptions import SandboxException, TimeoutException
    from e2b.sandbox.commands.command_handle import CommandExitException

    sess = _session(workspace_id, project)
    # 先装工具链再上传：装不上就早点回落容器，别白传一遍项目文件
    mvn_bin = _ensure_maven(sess) if lang == "maven" else ""
    skipped = _sync_up(sess, root, extra=script)

    if script is not None:
        rel = script.relative_to(root).as_posix() if script.is_relative_to(root) else script.name
        inner = _inner(sess.workdir, rel)
        cwd = sess.workdir
        if lang == "maven":
            pom_dir = script.parent if script.is_file() else script
            cwd = _inner(sess.workdir, pom_dir.relative_to(root).as_posix()) if pom_dir.is_relative_to(root) else sess.workdir
        # 云沙箱不传 -s：`/opt/mvn-settings.xml` 只存在于我们自己的镜像里，
        # 官方模板没有这个文件，传了反而报「settings 不存在」。离线与否靠 -o。
        argv = run_command(
            lang, inner, code,
            offline=not _allow_internet(),
            mvn_settings="",
            mvn_pom_dir="",
        )
    else:
        inner = sess.workdir
        cwd = sess.workdir
        argv = run_command(lang, inner, code, offline=not _allow_internet(), mvn_settings="")

    command = " ".join(shlex.quote(a) for a in argv)
    envs = {"PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    if lang == "maven":
        # 用探测到的绝对路径：模板里的 mvn 可能在 /opt/maven，不在 PATH 上
        command = " ".join(shlex.quote(a) for a in [mvn_bin or "mvn", *argv[1:]])
        envs["MAVEN_OPTS"] = MAVEN_OPTS

    try:
        res = sess.box.commands.run(command, cwd=cwd, envs=envs, timeout=secs, request_timeout=secs + 30)
        exit_code, out, err = res.exit_code, res.stdout or "", res.stderr or ""
    except CommandExitException as exc:      # 非零退出是正常结果，不是故障
        exit_code, out, err = exc.exit_code, exc.stdout or "", exc.stderr or ""
    except TimeoutException:
        return {
            "language": lang, "script": path_label, "exit_code": -1, "ok": False,
            "stdout": "", "stderr": f"执行超时（{secs}s）已被终止。检查是否有死循环或等待输入。",
            "output_truncated": False, "sandbox": "e2b",
        }
    except SandboxException as exc:
        # 会话中途失效：丢掉它，让下一次重建；本次仍按失败返回，不重跑（可能已有副作用）
        with _LOCK:
            _drop(_key(workspace_id, project), kill=True)
        raise CodeError(f"云沙箱执行中断：{exc}") from exc

    written, skipped_back = _sync_down(sess, root)
    sess.last_used = time.time()

    hint = offline_hint(lang, out + err, offline=not _allow_internet())
    out, cut_out = clip(out)
    err, cut_err = clip(err)
    if exit_code != 0 and hint:
        err += hint
    if skipped:
        err += "\n[沙箱提示] 以下文件未上传：" + "、".join(skipped[:5])
    if written:
        # 明确告诉模型哪些产物已落到本地项目，它才不会重复生成一遍
        out += "\n[沙箱同步] 已回写：" + "、".join(written[:8])
    if skipped_back:
        # 静默丢弃最坑：模型以为交付了，用户打开项目却什么都没有
        err += (
            f"\n[沙箱同步] {len(skipped_back)} 个文件未回写（单文件上限 8MB / "
            f"单次合计 16MB / 最多 {MAX_WRITE_BACK_FILES} 个）："
            + "、".join(skipped_back[:5])
        )

    return {
        "language": lang,
        "script": path_label,
        "exit_code": exit_code,
        "ok": exit_code == 0,
        "stdout": out,
        "stderr": err,
        "output_truncated": cut_out or cut_err,
        "sandbox": "e2b",
        # 本次真正落到本地项目的文件：上层据此发产物事件（前端卡片/预览）
        "files": written,
    }


def reset(workspace_id: str, project: str) -> None:
    """外部显式回收（测试或项目删除时用）。"""
    with _LOCK:
        _drop(_key(workspace_id, project), kill=True)
