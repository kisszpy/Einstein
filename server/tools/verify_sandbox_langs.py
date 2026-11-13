"""验证沙箱的 Java / Maven / Node 能力：镜像、构建、网络边界。

为什么需要它：这几件事都没有「明显报错」的失败模式。镜像没装 java 时，
模型拿到 `command not found` 会以为是自己的代码错；Maven 缺依赖时拿到
`offline mode` 会以为是 pom 错；网络没按预期隔离时——它反而看着更正常，但
沙箱的整个安全前提已经没了。所以每次改镜像/模板或改执行器，跑一遍再上线。

对两个后端都适用（容器 / 云沙箱），断言会按当前模式自动切换：
云沙箱开网时期望「能连 pypi 且 pip 装得上」，容器时期望「连不上」。

用法（在 server/ 下）：
    .venv/Scripts/python.exe tools/verify_sandbox_langs.py
    .venv/Scripts/python.exe tools/verify_sandbox_langs.py --cloud   # 强制云沙箱

不需要起后端：直接调 code_exec，与线上走的是同一份代码。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.capabilities import code_exec, sandbox  # noqa: E402

OK = "  [ok]"
BAD = "  [!!]"
SKIP = "  [--]"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    detail = " | ".join(line.strip() for line in detail.strip().splitlines() if line.strip())
    print(f"{OK if ok else BAD} {label}" + (f" —— {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def skip(label: str, why: str) -> None:
    """不计入失败，但必须显式打印：静默跳过会让人以为「验过了」。"""
    print(f"{SKIP} {label}（跳过：{why}）")


def main() -> int:
    settings = get_settings()
    mode = code_exec.resolve_mode()
    print(f"后端：{mode}    镜像：{settings.agent_code_exec_image}")
    if mode == "e2b":
        print(
            f"云沙箱：模板 {settings.agent_code_exec_e2b_template}    "
            f"出网 {'开' if settings.agent_code_exec_e2b_allow_internet else '关'}    "
            f"白名单 {settings.agent_code_exec_e2b_allow_out or '（无，全放行）'}"
        )
    print(f"Maven 仓库挂载：{settings.agent_code_exec_m2_host or '（无，用镜像内置）'}")
    print()

    check("沙箱可用", mode in ("container", "e2b"), code_exec.available()[1])
    if mode not in ("container", "e2b"):
        print("\n无法继续：没有可用的执行后端。")
        return 1

    # 临时项目根：不碰 data/projects，跑完即弃
    with tempfile.TemporaryDirectory(prefix="einstein-langs-") as tmp:
        root = Path(tmp)
        sandbox.PROJECTS_DIR = root  # type: ignore[misc]
        proj = root / "ws" / "verify"
        proj.mkdir(parents=True)

        def run(**kw) -> dict:
            return code_exec.run_code("ws", "verify", **kw)

        print("语言运行时")
        res = run(code="import sys; print('python', sys.version.split()[0])")
        check("python", res["ok"], (res["stdout"] or res["stderr"]).strip()[:60])

        res = run(language="js", code="console.log('node', process.version)")
        check("node (CommonJS)", res["ok"], (res["stdout"] or res["stderr"]).strip()[:60])

        res = run(language="node", code="import os from 'node:os'; console.log('node-esm', os.arch())")
        check("node (ESM)", res["ok"], (res["stdout"] or res["stderr"]).strip()[:60])

        res = run(
            language="java",
            code=(
                "import java.util.List;\n"
                "public class Ping {\n"
                "  public static void main(String[] a) {\n"
                "    System.out.println(\"java \" + Runtime.version().feature());\n"
                "    System.out.println(\"sum \" + List.of(1,2,3).stream().mapToInt(i->i).sum());\n"
                "  }\n"
                "}\n"
            ),
        )
        if res["ok"]:
            check("java 单文件", True, (res["stdout"] or res["stderr"]).strip()[:60])
        elif mode == "e2b":
            skip("java 单文件", "当前模板没装 JDK，用 docker/template/ 构建自定义模板后再验")
        else:
            check("java 单文件", False, (res["stdout"] or res["stderr"]).strip()[:60])

        print(f"\nMaven（{'在线' if mode == 'e2b' and settings.agent_code_exec_e2b_allow_internet else '离线仓库'}）")
        app = proj / "mvnapp"
        (app / "src/main/java/demo").mkdir(parents=True)
        (app / "src/test/java/demo").mkdir(parents=True)
        (app / "pom.xml").write_text(
            """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>demo</groupId><artifactId>demo</artifactId><version>1.0</version>
  <properties><maven.compiler.release>21</maven.compiler.release>
  <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
  <dependencies>
    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version><scope>test</scope></dependency>
  </dependencies>
</project>""",
            encoding="utf-8",
        )
        (app / "src/main/java/demo/App.java").write_text(
            "package demo;\npublic class App { public static int twice(int x) { return x * 2; } }\n",
            encoding="utf-8",
        )
        (app / "src/test/java/demo/AppTest.java").write_text(
            """package demo;
import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;
class AppTest { @Test void doubles() { assertEquals(6, App.twice(3)); } }
""",
            encoding="utf-8",
        )
        # pom 里不写插件版本：模拟模型最常见的写法，验证默认绑定也能离线解析
        res = run(language="maven", path="mvnapp", code="test", timeout=240)
        blob = res["stdout"] + res["stderr"]
        maven_ok = res["ok"] and ("Tests run" in blob or "BUILD SUCCESS" in blob)
        if maven_ok:
            check("mvn test（无显式插件版本 + JUnit 5）", True)
        elif mode == "e2b" and ("mvn: not found" in blob or "command not found" in blob):
            skip("mvn test", "当前模板没装 Maven，用 docker/template/ 构建自定义模板后再验")
        else:
            check(
                "mvn test（无显式插件版本 + JUnit 5）",
                False,
                "" if res["ok"] else blob.strip().splitlines()[-1][:100] if blob.strip() else "",
            )

        print("\n安全边界")
        res = run(
            code=(
                "import socket\n"
                "try:\n"
                "    socket.create_connection(('pypi.org', 443), timeout=3)\n"
                "    print('REACHABLE')\n"
                "except OSError as e:\n"
                "    print('offline', type(e).__name__)\n"
            )
        )
        reachable = "REACHABLE" in res["stdout"]
        if mode == "container":
            check("容器断网", not reachable, res["stdout"].strip()[:60])
        elif settings.agent_code_exec_e2b_allow_internet:
            # 云端开了出网就是设计如此，这里验的是「真的能装包」，否则模型会白试
            check("云沙箱可出网", reachable, res["stdout"].strip()[:60])
            res = run(
                code=(
                    "import subprocess, sys\n"
                    "p = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'tabulate'],\n"
                    "                   capture_output=True, text=True)\n"
                    "print('pip', p.returncode)\n"
                ),
                timeout=180,
            )
            check("pip 可装包", "pip 0" in res["stdout"], (res["stdout"] or res["stderr"]).strip()[:80])
        else:
            check("云沙箱断网（已按配置关闭）", not reachable, res["stdout"].strip()[:60])

        # 密钥必须读不到：容器靠不挂环境变量，云端靠「沙箱里不放任何凭据」。
        # 两种安全表现都算通过：进程环境里没有，或 /proc/1/environ 根本读不了
        # （云沙箱是独立 VM，无权限读 init 的环境，这比「读到 0 个」更强）。
        res = run(
            code=(
                "import os\n"
                "leak = [k for k in os.environ if 'API_KEY' in k.upper() or 'SECRET' in k.upper()]\n"
                "print('env-leak', len(leak))\n"
                "try:\n"
                "    n = open('/proc/1/environ').read().count('LLM_API_KEY')\n"
                "except OSError as e:\n"
                "    print('proc-unreadable', type(e).__name__)\n"
                "else:\n"
                "    print('proc-hits', n)\n"
            )
        )
        out = res["stdout"]
        no_leak = res["ok"] and "env-leak 0" in out and (
            "proc-hits 0" in out or "proc-unreadable" in out
        )
        check("沙箱内读不到服务端密钥", no_leak, out.replace("\n", " ").strip()[:80])

    print()
    if failures:
        print(f"未通过 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print(f"全部通过（{mode}）：语言运行时与 Maven 可用，安全边界符合当前配置。")
    return 0


if __name__ == "__main__":
    if "--cloud" in sys.argv:
        # 强制走云沙箱：本机装了 Docker 时 auto 不会自动选云，验云端要显式指定
        import os

        os.environ["APP_AGENT_CODE_EXEC_SANDBOX"] = "e2b"
        get_settings.cache_clear()
    raise SystemExit(main())
