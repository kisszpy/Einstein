"""云沙箱后端：会话复用、文件同步、命令拼装、离线/在线两种姿态。

这里全部用**假沙箱**，不发网络请求：真机验证（要 Key 与地域）另放在
`tools/verify_sandbox_langs.py` 的 --cloud 分支里。单测要能在 CI 上跑，
不能因为「没配 Key」就红。

钉住的关键行为：
1. 上传要跳过 node_modules / .git 这类目录，否则一个前端项目能传几百 MB；
2. 只有「命令改过」的文件才回写，别把刚传上去的原样拉回来；
3. 云端可出网时**不加** `-o`/`-s`，不可出网时才加——这条错了模型会
   拿到一句「offline mode」然后放弃重试。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.capabilities import e2b_sandbox, sandbox_common


class _Files:
    def __init__(self, box: "FakeBox") -> None:
        self.box = box

    def write(self, path: str, data) -> None:
        self.box.written[path] = data if isinstance(data, bytes) else str(data).encode()

    def list(self, path: str, depth: int = 1):
        return list(self.box.entries)

    def read(self, path: str, format: str = "text"):
        return self.box.contents.get(path, b"")


class _Commands:
    def __init__(self, box: "FakeBox") -> None:
        self.box = box

    def run(self, cmd, cwd=None, envs=None, timeout=None, request_timeout=None, **_):
        self.box.calls.append(SimpleNamespace(cmd=cmd, cwd=cwd, envs=envs or {}))
        if cmd.startswith("mkdir -p"):
            return SimpleNamespace(exit_code=0, stdout="", stderr="")
        return SimpleNamespace(exit_code=0, stdout="hello\n", stderr="")


class FakeBox:
    sandbox_id = "fake-box"

    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}
        self.contents: dict[str, bytes] = {}
        self.entries: list = []
        self.calls: list = []
        self.killed = False
        self.files = _Files(self)
        self.commands = _Commands(self)

    def kill(self) -> None:
        self.killed = True


def _entry(rel: str, size: int, mtime: float, workdir: str = "/home/user/work"):
    return SimpleNamespace(
        type="file",
        path=f"{workdir}/{rel}",
        name=rel.rsplit("/", 1)[-1],
        size=size,
        modified_time=datetime.fromtimestamp(mtime, tz=timezone.utc),
    )


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """装一个假会话：绕过真实建会话，专注测同步与命令。"""
    box = FakeBox()
    sess = e2b_sandbox._Session(box=box, workdir="/home/user/work", last_used=time.time())
    monkeypatch.setattr(e2b_sandbox, "_session", lambda *a, **k: sess)
    monkeypatch.setattr(e2b_sandbox, "_allow_internet", lambda: True)
    return box, sess, tmp_path


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / ".agent_tmp").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "big.js").write_text("x" * 100, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------- 模式选择

def test_auto_prefers_cloud_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capabilities import code_exec

    monkeypatch.setattr(e2b_sandbox, "api_key", lambda: "sk-test")
    monkeypatch.setattr(code_exec, "docker_ready", lambda *a, **k: (True, "28.0"))
    assert code_exec.resolve_mode() == "e2b", "配了 Key 却仍走 docker，等于配置没生效"


def test_explicit_cloud_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from app.services.capabilities import code_exec

    monkeypatch.setattr(get_settings(), "agent_code_exec_sandbox", "e2b", raising=False)
    monkeypatch.setattr(e2b_sandbox, "api_key", lambda: "")
    assert code_exec.resolve_mode() == "unavailable"


def test_available_message_names_both_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from app.services.capabilities import code_exec

    monkeypatch.setattr(get_settings(), "agent_code_exec_sandbox", "auto", raising=False)
    monkeypatch.setattr(e2b_sandbox, "api_key", lambda: "")
    monkeypatch.setattr(code_exec, "docker_ready", lambda *a, **k: (False, "未安装 docker"))
    ok, why = code_exec.available()
    assert not ok and "云沙箱" in why and "docker" in why


# --------------------------------------------------------------- 文件同步

def test_upload_skips_dependency_dirs(fake) -> None:
    box, sess, root = fake
    _project(root)
    e2b_sandbox._sync_up(sess, root)
    assert "/home/user/work/src/main.py" in box.written
    assert not any("node_modules" in p for p in box.written), "依赖目录传上去纯浪费带宽"


def test_upload_is_incremental(fake) -> None:
    box, sess, root = fake
    _project(root)
    e2b_sandbox._sync_up(sess, root)
    first = len(box.written)
    e2b_sandbox._sync_up(sess, root)
    assert len(box.written) == first, "文件没变就不要再传一遍"


def test_changed_file_is_written_back(fake, tmp_path: Path) -> None:
    box, sess, root = fake
    _project(root)
    e2b_sandbox._sync_up(sess, root)

    out = root / "result.csv"
    box.contents["/home/user/work/result.csv"] = b"a,b\n1,2\n"
    box.entries = [_entry("result.csv", 8, time.time() + 5)]

    written, skipped = e2b_sandbox._sync_down(sess, root)
    assert written == ["result.csv"]
    assert skipped == []
    assert out.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_untouched_upload_is_not_downloaded(fake) -> None:
    box, sess, root = fake
    _project(root)
    e2b_sandbox._sync_up(sess, root)

    size = (root / "src" / "main.py").stat().st_size
    box.contents["/home/user/work/src/main.py"] = b"print(1)\n"
    box.entries = [_entry("src/main.py", size, time.time() - 1)]

    written, _skipped = e2b_sandbox._sync_down(sess, root)
    assert written == [], "刚传上去的文件再拉一遍是纯浪费"


def test_big_file_is_reported_not_silently_dropped(fake, monkeypatch: pytest.MonkeyPatch) -> None:
    box, sess, root = fake
    monkeypatch.setattr(e2b_sandbox, "MAX_FILE_BYTES", 4)
    (root / "huge.bin").write_bytes(b"0123456789")
    skipped = e2b_sandbox._sync_up(sess, root)
    assert skipped and any("huge.bin" in s for s in skipped)


class _FakeEnum:
    """模拟 e2b 的 FileType：str() 出来是 "FileType.FILE"，不等于 "file"。"""

    def __init__(self, label: str) -> None:
        self.label = label

    def __str__(self) -> str:
        return self.label


def test_filetype_enum_still_counts_as_file(fake) -> None:
    """回归：曾按 `type != "file"` 判断，枚举永远不等，回写一条都不做。"""
    box, sess, root = fake
    _project(root)
    e2b_sandbox._sync_up(sess, root)

    box.contents["/home/user/work/r.txt"] = b"ok"
    entry = _entry("r.txt", 2, time.time() + 5)
    entry.type = _FakeEnum("FileType.FILE")
    box.entries = [entry]

    written, _skipped = e2b_sandbox._sync_down(sess, root)
    assert written == ["r.txt"]


def test_bytearray_content_is_written_raw(fake) -> None:
    """回归：format="bytes" 返回 bytearray，被当成 str 写成 'bytearray(b"…")'。"""
    box, sess, root = fake
    _project(root)
    e2b_sandbox._sync_up(sess, root)

    box.contents["/home/user/work/b.bin"] = bytearray(b"\x01\x02\x00\x03")
    box.entries = [_entry("b.bin", 4, time.time() + 5)]

    written, _skipped = e2b_sandbox._sync_down(sess, root)
    assert written == ["b.bin"]
    assert (root / "b.bin").read_bytes() == b"\x01\x02\x00\x03"


def test_build_output_is_not_written_back(fake) -> None:
    """构建中间产物不拉回本地：几十个 .class 只是噪音，下次构建还会覆盖。"""
    box, sess, root = fake
    _project(root)
    box.contents["/home/user/work/target/classes/A.class"] = b"\xca\xfe"
    box.entries = [_entry("target/classes/A.class", 2, time.time() + 5)]
    written, _skipped = e2b_sandbox._sync_down(sess, root)
    assert written == []
    assert not (root / "target").exists()


def test_delivery_dirs_are_written_back(fake) -> None:
    """回归：`out/` `dist/` `build/` 曾被当成构建垃圾过滤，交付物直接丢失。

    它们名字像中间产物，实际是很多项目的**输出目录**（前端打包、导出报告）。
    以前在沙箱里生成的网页/图表因此从没回来过，用户看到的是「什么都没交付」。
    """
    box, sess, root = fake
    _project(root)
    for rel in ("out/report.md", "dist/app.js", "build/x.txt"):
        box.contents[f"/home/user/work/{rel}"] = b"ok"
    box.entries = [_entry(rel, 2, time.time() + 5)
                   for rel in ("out/report.md", "dist/app.js", "build/x.txt")]

    written, skipped = e2b_sandbox._sync_down(sess, root)
    assert sorted(written) == ["build/x.txt", "dist/app.js", "out/report.md"]
    assert skipped == []
    for rel in written:
        assert (root / rel).read_bytes() == b"ok"


def test_oversized_delivery_is_reported_not_silently_dropped(fake, monkeypatch: pytest.MonkeyPatch) -> None:
    """大文件回写不了也必须说一声：静默丢弃等于告诉模型「交付成功」。"""
    box, sess, root = fake
    _project(root)
    monkeypatch.setattr(e2b_sandbox, "MAX_FILE_BYTES", 4)
    box.contents["/home/user/work/big.pptx"] = b"0123456789"
    box.entries = [_entry("big.pptx", 10, time.time() + 5)]

    written, skipped = e2b_sandbox._sync_down(sess, root)
    assert written == []
    assert skipped == ["big.pptx"]


# --------------------------------------------------------------- 命令拼装

def test_cloud_command_quotes_and_runs_in_workdir(fake, tmp_path: Path) -> None:
    box, sess, root = fake
    _project(root)
    script = root / "src" / "main.py"
    res = e2b_sandbox.run(
        root, script, "python", 30, "", "src/main.py", "ws", "proj",
    )
    call = [c for c in box.calls if not c.cmd.startswith("mkdir -p")][-1]
    assert call.cmd == "python3 -X utf8 /home/user/work/src/main.py"
    assert call.cwd == "/home/user/work"
    assert res["ok"] and res["sandbox"] == "e2b"


def test_run_returns_written_files_for_artifact_cards(fake, tmp_path: Path) -> None:
    """回写的文件要作为结构化字段交给上层，前端才有产物卡片可点。

    以前只把文件名拼进 stdout 文本里：模型知道文件在，前端不知道——用户
    右侧看不到卡片，done 帧里也没有，表现就是「跑完了什么都没交付」。
    """
    box, sess, root = fake
    _project(root)
    box.contents["/home/user/work/out/chart.svg"] = b"<svg/>"
    box.entries = [_entry("out/chart.svg", 6, time.time() + 5)]

    res = e2b_sandbox.run(
        root, root / "src" / "main.py", "python", 30, "", "src/main.py", "ws", "proj",
    )

    assert res["files"] == ["out/chart.svg"]
    assert (root / "out" / "chart.svg").read_bytes() == b"<svg/>"
    assert "已回写：out/chart.svg" in res["stdout"]


def test_maven_offline_flag_follows_internet_switch() -> None:
    online = sandbox_common.run_command("maven", "/w/p", "package", offline=False, mvn_settings="")
    assert "-o" not in online and "-s" not in online
    offline = sandbox_common.run_command("maven", "/w/p", "package", offline=True, mvn_settings="")
    assert "-o" in offline


def test_maven_runs_with_installed_binary(fake, monkeypatch: pytest.MonkeyPatch) -> None:
    """官方模板没有 mvn，装完要用绝对路径起，不能指望 PATH 里有。"""
    box, sess, root = fake
    _project(root)
    monkeypatch.setattr(e2b_sandbox, "_ensure_maven", lambda s: "/opt/maven/bin/mvn")
    e2b_sandbox.run(root, root, "maven", 120, "package", ".", "ws", "proj")
    call = [c for c in box.calls if not c.cmd.startswith("mkdir -p")][-1]
    assert call.cmd.startswith("/opt/maven/bin/mvn -B package")


def test_missing_maven_fails_fast_without_reinstalling(fake) -> None:
    """装失败过就别再试第二次：每次重试都要多等 100 多秒的下载超时。"""
    _box, sess, _root = fake
    sess.maven = "missing:模板没有 curl"
    with pytest.raises(sandbox_common.CodeError):
        e2b_sandbox._ensure_maven(sess)
    assert sess.box.calls == [], "已知不可用时不该再往沙箱发命令"


def test_network_whitelist_builds_default_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    """有白名单时必须同时给 deny 0.0.0.0/0，否则「白名单」只是安慰剂。"""
    import e2b

    captured: dict = {}

    class FakeSandbox:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return FakeBox()

    monkeypatch.setattr(e2b, "Sandbox", FakeSandbox)
    monkeypatch.setattr(e2b_sandbox, "api_key", lambda: "sk-test")
    monkeypatch.setattr(e2b_sandbox, "_allow_out", lambda: ["pypi.org", "registry.npmjs.org"])

    e2b_sandbox._create_box()
    assert captured["network"] == {
        "allow_out": ["pypi.org", "registry.npmjs.org"],
        "deny_out": ["0.0.0.0/0"],
    }
    assert captured["allow_internet_access"] is True


def test_no_whitelist_keeps_default_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """不填白名单就不传 network，交给服务端默认（全开），别塞一个意外的 deny。"""
    import e2b

    captured: dict = {}

    class FakeSandbox:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return FakeBox()

    monkeypatch.setattr(e2b, "Sandbox", FakeSandbox)
    monkeypatch.setattr(e2b_sandbox, "api_key", lambda: "sk-test")
    monkeypatch.setattr(e2b_sandbox, "_allow_out", lambda: [])

    e2b_sandbox._create_box()
    assert "network" not in captured
