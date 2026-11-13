"""真实容器里的多语言端到端验证。

默认跳过：必须显式 `EINSTEIN_SANDBOX_E2E=1` 才跑，理由是它依赖本机 docker
且每次要拉起容器（Maven 一条 10-30s）。CI 里没有 docker 也不该变红。

这些用例存在的意义是钉住「镜像里真的有三套运行时」：
单元测试只能证明参数拼得对，证明不了 `java` 在 PATH 里、`mvn -o` 能解析到
预热好的依赖、node 的 ESM/CJS 两条路都走得通。这三件事一旦回退，表现都
是模型收到 `command not found`，然后误以为自己写错了代码。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.capabilities import code_exec, sandbox

E2E = os.getenv("EINSTEIN_SANDBOX_E2E") == "1"


def _image() -> str:
    return getattr(get_settings(), "agent_code_exec_image", "") or "einstein-code-sandbox:latest"


def _skip_reason() -> str | None:
    if not E2E:
        return "未设置 EINSTEIN_SANDBOX_E2E=1"
    ok, why = code_exec.docker_ready()
    if not ok:
        return f"docker 不可用：{why}"
    exe = shutil.which("docker") or "docker"
    if subprocess.run([exe, "image", "inspect", _image()], capture_output=True).returncode != 0:
        return f"镜像 {_image()} 不存在，先 docker build"
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=str(_skip_reason()))


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的项目目录，避免 E2E 在 data/projects 里留垃圾。"""
    monkeypatch.setattr(sandbox, "PROJECTS_DIR", tmp_path)
    root = tmp_path / "ws" / "demo"
    root.mkdir(parents=True)
    code_exec._DOCKER.update({"checked": True, "ok": True, "note": "e2e"})
    monkeypatch.setattr(code_exec, "resolve_mode", lambda: "container")
    return root


def _run(project: Path, **kwargs) -> dict:
    return code_exec.run_code("ws", "demo", **kwargs)


def test_python_still_works_with_data_libs(project: Path) -> None:
    res = _run(project, code="import pandas as pd\nprint(pd.DataFrame({'a':[1,2]}).a.sum())")
    assert res["ok"], res["stderr"]
    assert "3" in res["stdout"]


def test_node_commonjs_require(project: Path) -> None:
    """模型写 node 十有八九是 require() 心智，这条路必须先通。"""
    res = _run(project, language="js", code="const fs=require('node:fs');console.log('node-ok', typeof fs.readFileSync);")
    assert res["ok"], res["stderr"]
    assert "node-ok function" in res["stdout"]


def test_node_esm_import(project: Path) -> None:
    res = _run(project, language="node", code="import os from 'node:os';console.log('esm-ok', os.platform().length>0);")
    assert res["ok"], res["stderr"]
    assert "esm-ok true" in res["stdout"]


def test_java_single_file_source(project: Path) -> None:
    """JDK 11+ 的单文件模式：不用 javac、不用 classpath，直接 java Foo.java。"""
    src = """
import java.util.List;
public class Demo {
    public static void main(String[] args) {
        List<Integer> xs = List.of(3, 1, 2);
        System.out.println("java-ok " + xs.stream().mapToInt(i -> i).sum());
    }
}
"""
    res = _run(project, language="java", code=src)
    assert res["ok"], res["stderr"]
    assert "java-ok 6" in res["stdout"]


def test_maven_offline_build_uses_seeded_repo(project: Path) -> None:
    """mvn 必须在断网下用 /opt/m2 里预热好的 junit 跑完 test 阶段。

    这条同时验证三件事：mvn 在 PATH 里、settings 的离线仓库指向正确、
    预热清单里的 junit-jupiter 真的能用。任何一件坏了，模型写 Java 交付物
    都会卡在一个看起来像「pom 写错了」的错误上。
    """
    proj = project / "mvnproj"
    (proj / "src" / "main" / "java" / "demo").mkdir(parents=True)
    (proj / "src" / "test" / "java" / "demo").mkdir(parents=True)
    (proj / "pom.xml").write_text(
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
    (proj / "src/main/java/demo/App.java").write_text(
        "package demo;\npublic class App { public static int twice(int x){return x*2;} }\n",
        encoding="utf-8",
    )
    (proj / "src/test/java/demo/AppTest.java").write_text(
        """package demo;
import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;
class AppTest {
    @Test void doubles() { assertEquals(6, App.twice(3)); }
}
""",
        encoding="utf-8",
    )

    res = _run(project, language="maven", path="mvnproj", code="test", timeout=180)
    # Maven 把 [ERROR] 打在 stdout，只看 stderr 会得到一片空白
    assert res["ok"], (res["stderr"] + "\n--- stdout ---\n" + res["stdout"])[-3000:]
    assert "BUILD SUCCESS" in res["stdout"] or "Tests run" in res["stdout"]


def test_container_has_no_network(project: Path) -> None:
    """断网是安全边界而不是环境故障，别在给沙箱加语言时把它一起放开。"""
    res = _run(
        project,
        code=(
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('maven.aliyun.com', 443), timeout=3)\n"
            "    print('NETWORK-REACHABLE')\n"
            "except OSError as e:\n"
            "    print('offline', type(e).__name__)\n"
        ),
    )
    assert "offline" in res["stdout"], res["stdout"] + res["stderr"]
