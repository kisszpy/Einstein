"""多语言代码沙箱：语言路由、Maven 参数拼装、离线提示。

背景：沙箱是 `--network none` 的一次性容器，模型没法在里面 `pip install` /
`npm install` / 拉 Maven 依赖。它写 Java 交付物时拿到的是
`java: command not found`，写 Node 时是 `node: command not found`——这类
错误看起来像「模型不会写代码」，实际是运行时没装。所以三套工具链必须
烘进镜像，执行器也必须真的会把语言路由到位。

这里钉死的是**纯逻辑**部分（不依赖 docker，任何环境都能跑）：
别名归一、mvn 参数不重复注入、不把 `&& rm -rf /` 当生命周期阶段、
Java 单文件命名、Node 按 ESM/CJS 挑后缀、离线错误只在该补话时补。

真实容器执行见 test_code_exec_container.py（需 EINSTEIN_SANDBOX_E2E=1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.capabilities import code_exec


# --------------------------------------------------------------- 语言别名

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("python", "python"),
        ("py", "python"),
        ("python3", "python"),
        ("PYTHON", "python"),
        ("js", "node"),
        ("nodejs", "node"),
        ("javascript", "node"),
        ("mjs", "node"),
        ("java", "java"),
        ("javac", "java"),
        ("mvn", "maven"),
        ("maven", "maven"),
        ("", "python"),  # 没给就按默认，别把最简单的一次调用变成错误
    ],
)
def test_language_alias_normalized(raw: str, expected: str) -> None:
    assert code_exec.normalize_lang(raw) == expected


def test_unknown_language_rejected_with_options() -> None:
    with pytest.raises(code_exec.CodeError) as err:
        code_exec.normalize_lang("rust")
    # 报错要带可用清单，模型才知道下一步该改成什么
    assert "python" in str(err.value) and "maven" in str(err.value)


# ---------------------------------------------------------- Maven 参数拼装

def test_maven_defaults_are_offline_and_point_at_seed_repo() -> None:
    args = code_exec._maven_args("", "/work")
    assert "-o" in args, "容器断网，不加 -o 会烧几十秒连接超时"
    assert "-B" in args
    assert args[-2:] == ["-s", "/opt/mvn-settings.xml"] or "-s" in args
    assert "compile" in args, "只给 flag 时要补一个默认 goal，否则 mvn 直接报用法错误"


def test_maven_strips_command_prefix() -> None:
    args = code_exec._maven_args("mvn -q package", "/work")
    assert "mvn" not in args
    assert "package" in args


def test_maven_truncates_shell_chain_instead_of_passing_it_through() -> None:
    """没有 shell，`&&` 之后的内容不能被当成生命周期阶段传给 mvn。

    否则错误信息是「Unknown lifecycle phase "rm"」，模型会以为自己写错了
    goal 名，而不是「这里不支持管道」。
    """
    args = code_exec._maven_args("compile && rm -rf /", "/work")
    assert "compile" in args
    assert "rm" not in args and "&&" not in args and "/" not in args


def test_maven_does_not_duplicate_explicit_flags() -> None:
    args = code_exec._maven_args("-o -B test", "/work")
    assert args.count("-o") == 1 and args.count("-B") == 1


def test_maven_adds_f_only_for_subdirectory_project() -> None:
    root_args = code_exec._maven_args("package", "/work")
    assert "-f" not in root_args  # 容器工作目录就是 /work，重复指定只会更脆

    sub_args = code_exec._maven_args("package", "/work/demo")
    assert sub_args[-2:] == ["-f", "/work/demo/pom.xml"]


def test_maven_host_mode_skips_container_settings() -> None:
    """宿主模式没有 /opt/mvn-settings.xml，带上它只会让 mvn 找不到文件。"""
    args = code_exec._maven_args("package", "", settings_path="")
    assert "/opt/mvn-settings.xml" not in args


# ------------------------------------------------- 落盘文件名（语言相关）

def test_java_file_named_after_public_class(tmp_path: Path) -> None:
    script, cleanup = code_exec._prepare_script(
        "java", "public class ReportBuilder { public static void main(String[] a){} }", tmp_path
    )
    assert script.name == "ReportBuilder.java"
    assert cleanup is True


def test_java_without_public_class_falls_back_to_main(tmp_path: Path) -> None:
    script, _ = code_exec._prepare_script("java", "class X { }", tmp_path)
    assert script.name == "Main.java"


@pytest.mark.parametrize(
    "code,suffix",
    [
        ("const fs = require('fs'); console.log(1);", ".cjs"),
        ("import fs from 'node:fs'; console.log(1);", ".mjs"),
        ("export const a = 1;", ".mjs"),
        ("await Promise.resolve(1);", ".cjs"),
    ],
)
def test_node_suffix_matches_module_syntax(tmp_path: Path, code: str, suffix: str) -> None:
    """后缀挑错 → 一句和代码本意无关的语法错误（require is not defined）。"""
    script, _ = code_exec._prepare_script("node", code, tmp_path)
    assert script.suffix == suffix


# ------------------------------------------------------------ 离线提示

def test_hint_only_when_signature_matches() -> None:
    assert code_exec._offline_hint("python", "ZeroDivisionError: division by zero") == ""
    assert "pandas" in code_exec._offline_hint("python", "ModuleNotFoundError: No module named 'seaborn'")
    assert "npm install" in code_exec._offline_hint("node", "Error: Cannot find module 'lodash'")
    assert "单文件" in code_exec._offline_hint("java", "package com.google.gson does not exist")
    assert "离线模式" in code_exec._offline_hint("maven", "Cannot access central in offline mode")


def test_node_module_syntax_hint_is_actionable() -> None:
    esm_in_cjs = code_exec._offline_hint("node", "SyntaxError: Cannot use import statement outside a module")
    assert "require" in esm_in_cjs
    cjs_in_esm = code_exec._offline_hint("node", "ReferenceError: require is not defined")
    assert "import" in cjs_in_esm


# ------------------------------------------------------ 工具装配一致性

def test_available_reports_languages_in_container_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """装配期与执行期必须同一结论：报了容器可用，就真的四种语言都能路由。"""
    monkeypatch.setattr(code_exec, "resolve_mode", lambda: "container")
    ok, why = code_exec.available()
    assert ok and "Maven" in why
