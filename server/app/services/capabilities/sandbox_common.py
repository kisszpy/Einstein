"""沙箱后端的共用纯逻辑：语言归一、落盘命名、命令拼装、错误提示。

为什么单独拆出来：Docker、宿主、云沙箱三个后端要用同一套「语言怎么落到文件
名」「mvn 参数怎么拼」「报错该不该补一句人话」。如果各写一份，最先分叉的
一定是 Maven 参数——云端可联网不需要 `-o`，本地必须 `-o`，两边一旦各改各的，
模型看到的行为就会随部署方式漂移。

这里**不含任何 IO**，也不依赖 docker / e2b，任何环境都能直接单测。
真正的执行分别在 `code_exec._run_in_container` / `_run_on_host` 与
`e2b_sandbox.run`。
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path

MAX_TIMEOUT = 120          # 普通脚本
MAX_BUILD_TIMEOUT = 300    # JVM 构建（编译 + 测试天然更慢）
DEFAULT_TIMEOUT = 60
MAX_OUTPUT = 8000

# 模型可能写 js / py / mvn / nodejs。别名不归一就会在两处同时出错：
# 工具层报「不支持该语言」、模型却觉得自己用的是受支持的语言。
LANG_ALIASES = {
    "py": "python", "python": "python", "python3": "python",
    "js": "node", "mjs": "node", "cjs": "node", "javascript": "node",
    "node": "node", "nodejs": "node",
    "java": "java", "javac": "java", "jdk": "java",
    "mvn": "maven", "maven": "maven",
}
LANGUAGES = ("python", "node", "java", "maven")
LANG_LABEL = {"python": "Python", "node": "Node.js", "java": "Java", "maven": "Maven"}

# JVM 参数：沙箱里跑的都是短命进程，关掉 perf 文件（根只读会写失败）、
# 关掉 C2（省启动时间）、开 CDS，并固定编码——不固定的话容器默认
# POSIX locale 下中文输出会变成 ???。
JVM_FLAGS = [
    "-XX:-UsePerfData",
    "-XX:TieredStopAtLevel=1",
    "-Xshare:auto",
    "-Dfile.encoding=UTF-8",
    "-Dstdout.encoding=UTF-8",
    "-Dstderr.encoding=UTF-8",
]

KEEP_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "COMSPEC", "HOME", "USERPROFILE", "LANG")

# Maven 的内存预算：容器里 1G 上限下默认堆会把容器打爆（表现是无输出 + 137），
# 云沙箱规格更大但没必要吃满，统一给死一个够用又不浪费的值。
MAVEN_OPTS = "-Xmx320m -XX:MaxMetaspaceSize=192m -XX:-UsePerfData -Dfile.encoding=UTF-8"

# 判断 node 代码该按 ESM 还是 CJS 落地。模型两种写法都会用：写 `import` 而
# 文件是 .cjs 会 "Cannot use import statement outside a module"，反过来写
# `require` 而文件是 .mjs 会 "require is not defined"。
ESM_RE = re.compile(r"^\s*(?:import\s+[\w{*'\"$]|export\s+(?:default|const|function|class|\{)|import\s*\()", re.M)
JAVA_CLASS_RE = re.compile(
    r"\bpublic\s+(?:final\s+|abstract\s+|sealed\s+|non-sealed\s+)*"
    r"(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)"
)


class CodeError(Exception):
    """可读的执行错误：直接回给模型看，所以措辞要有下一步。"""


def normalize_lang(language: str) -> str:
    raw = (language or "python").strip().lower()
    lang = LANG_ALIASES.get(raw)
    if not lang:
        raise CodeError(
            f"不支持的语言「{language}」。可用：{', '.join(LANGUAGES)}"
            "（别名 python3 / js / nodejs / javac / mvn 也可以）"
        )
    return lang


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_COLOR"] = "1"
    return env


def clip(text: str, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n…（中间省略）…\n" + text[-half:], True


def maven_args(
    raw: str,
    pom_dir: str = "",
    settings_path: str = "/opt/mvn-settings.xml",
    offline: bool = True,
) -> list[str]:
    """把模型给的一串东西变成 mvn 的参数数组。

    这里**不走 shell**：整段用 `shlex.split` 拆成 argv 直接交给 docker exec
    形式，`;`、`&&`、`$(...)` 都只是普通字符串，不会被解释。所以模型写
    `package && rm -rf /` 也只会在 mvn 那里报「未知生命周期阶段」。

    `offline=True` 是容器后端的默认：容器断网，不加 `-o` 会白等几十秒连接超时。
    云沙箱可出网，调用方传 False，让 Maven 正常在线解析。
    """
    args = shlex.split((raw or "").strip())
    # 模型常把整条命令写进来（"mvn -q package"），前缀剥掉
    while args and args[0].split("/")[-1] in ("mvn", "mvnw", "maven"):
        args = args[1:]
    # 有连接符就只取第一段：这里没有 shell，留着的 `rm -rf /` 会变成 mvn 的
    # 未知目标，报出来像「我不支持这个生命周期阶段」，与模型本意差很远
    for i, a in enumerate(args):
        if a in ("&&", ";", "|", "||"):
            args = args[:i]
            break

    def has(*names: str) -> bool:
        return any(a == n or a.startswith(n + "=") for a in args for n in names)

    if offline and not has("-o", "--offline"):
        args.insert(0, "-o")
    if not has("-B", "--batch-mode"):
        args.insert(0, "-B")
    # 补 goal 必须在拼 -s/-f 之前：它们的取值（路径）不以 - 开头，
    # 拼进去之后再看「有没有非 flag 参数」就永远是 True 了。
    if not any(not a.startswith("-") for a in args):
        args.append("compile")  # 只给了 flag 没给 goal，补一个最常见的
    if settings_path and not has("-s", "--settings"):
        args += ["-s", settings_path]
    if pom_dir and pom_dir != "/work" and not has("-f", "--file"):
        args += ["-f", f"{pom_dir}/pom.xml"]
    return args


def offline_hint(lang: str, err: str, offline: bool = True) -> str:
    """按语言给出「这一步为什么会失败」的说明。

    断网是容器后端的设计而不是故障，但报错原文（ENOTFOUND / offline mode /
    package does not exist）在模型眼里像是自己写错了，会反复重试同一条路。
    所以只在**命中特征**时补一段话，不无脑往每条错误后面贴。

    云沙箱能出网时（offline=False）给的是另一套话术：这时候装不上通常是
    包名写错或版本不存在，让它先 pip install 而不是「放弃并改用标准库」。
    """
    low = err.lower()
    if lang == "python" and ("modulenotfounderror" in low or "no module named" in low):
        if offline:
            return (
                "\n[沙箱提示] 容器是离线纯净镜像，第三方库只有 pandas / numpy / openpyxl / "
                "python-docx / python-pptx / matplotlib / tabulate。其余请用标准库实现；"
                "确需其它库，让部署方加进 APP_AGENT_CODE_EXEC_IMAGE 的镜像。"
            )
        return (
            "\n[沙箱提示] 沙箱可出网，缺库时直接 `pip install <包名>`"
            "（沙箱在云端，装在沙箱里即用即弃，不会污染你的项目）。"
        )
    if lang == "node" and any(k in low for k in ("cannot find module", "npm err", "enotfound", "eai_again", "getaddrinfo")):
        if offline:
            return (
                "\n[沙箱提示] 容器断网，`npm install` 不可用，只能用 Node 内置模块"
                "（fs / path / http / crypto / child_process 等）与预装的 typescript、tsx。"
                "第三方包若项目里已有 node_modules 可直接 require，没有就无法安装。"
            )
        return "\n[沙箱提示] 沙箱可出网，缺包时直接 `npm install <包名>`。"
    if lang == "node" and "cannot use import statement" in low:
        return "\n[沙箱提示] 这份代码按 ESM 写的但被当成 CommonJS 跑了。改成 require() 写法，或去掉 import 用动态 import()。"
    if lang == "node" and "require is not defined" in low:
        return "\n[沙箱提示] 这份代码是 CommonJS 写法但被当成 ESM 跑了。把 require 换成 import，或改成 module.exports 之外不要用 require。"
    if lang == "java" and ("package " in low and "does not exist" in low):
        return (
            "\n[沙箱提示] 单文件模式（java Xxx.java）不会自动引入第三方 jar，只能 import JDK 自带包"
            "（java.util / java.io / java.nio / java.time / java.net 等）。"
            "要用 Jackson、POI 这类库，请改用 language=\"maven\" 在带 pom.xml 的项目里构建。"
        )
    if lang == "java" and ("command not found" in low or "java: not found" in low):
        return "\n[沙箱提示] 当前沙箱模板里没有 JDK。让部署方换成带 OpenJDK 的模板，或改用 language=\"maven\"。"
    if lang == "maven" and any(k in low for k in ("offline mode", "could not resolve", "non-resolvable", "not resolve")):
        if offline:
            return (
                "\n[沙箱提示] 容器断网，Maven 处于离线模式，只能用镜像 /opt/m2 里预热好的依赖："
                "junit-jupiter、lombok、jackson-databind、gson、slf4j+logback、commons-lang3、"
                "commons-io、commons-csv、poi-ooxml、sqlite-jdbc、h2，以及 compiler/surefire/"
                "jar/shade/assembly/exec 插件。poi-ooxml 覆盖 Excel/Word。"
                "需要别的坐标时：要么让部署方加进 docker/m2-seed/pom.xml 重建镜像，"
                "要么用 APP_AGENT_CODE_EXEC_M2_HOST 把宿主 ~/.m2 挂进来。"
            )
        return (
            "\n[沙箱提示] 依赖解析失败。沙箱可出网，先确认坐标与版本存在"
            "（可先跑 `mvn -B dependency:resolve` 看具体是哪个 artifact 拉不到）。"
        )
    return ""


def prepare_script(lang: str, code: str, tmp_dir: Path) -> tuple[Path, bool]:
    """把代码落成一个文件，返回 (路径, 是否临时文件)。

    文件名不是随便取的：
    - java 单文件模式虽然允许文件名与类名不一致，但 `javac` 兜底路径要求一致，
      直接用 public 类名命名，两条路都通；
    - node 要按 ESM/CJS 语法挑后缀，挑错了就是一句与本意无关的语法错误。
    """
    if lang == "java":
        cls = (JAVA_CLASS_RE.search(code or "") or [None, "Main"])[1]
        script = tmp_dir / f"{cls}.java"
        script.write_text(code, encoding="utf-8")
        return script, True
    if lang == "node":
        suffix = ".mjs" if ESM_RE.search(code or "") else ".cjs"
    elif lang == "python":
        suffix = ".py"
    else:
        suffix = ".txt"
    fd, tmp = tempfile.mkstemp(dir=tmp_dir, suffix=suffix)
    os.close(fd)
    script = Path(tmp)
    script.write_text(code, encoding="utf-8")
    return script, True


def run_command(lang: str, inner: str, code: str, offline: bool = True,
                mvn_settings: str = "/opt/mvn-settings.xml",
                mvn_pom_dir: str = "") -> list[str]:
    """按语言拼出「在沙箱里怎么跑这个文件」的 argv。

    `inner` 是沙箱内路径。两个后端（容器 / 云沙箱）都调它，保证同一份代码
    在两种部署下走同一条命令。
    """
    if lang == "python":
        return ["python3", "-X", "utf8", inner]
    if lang == "node":
        # 项目里已有的 .ts 直接交给 tsx（镜像预装），别逼模型先编译一遍
        if inner.endswith((".ts", ".mts", ".cts")):
            return ["tsx", inner]
        return ["node", "--no-warnings", inner]
    if lang == "java":
        return ["java", *JVM_FLAGS, inner]
    return ["mvn", *maven_args(code, mvn_pom_dir, settings_path=mvn_settings, offline=offline)]
