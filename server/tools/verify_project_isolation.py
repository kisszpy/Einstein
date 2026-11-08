"""验证项目边界：智能体不得穿到未挂载的项目。

直接驱动 _WorkspaceBackend（不跑 LLM）：
1. 未挂载项目时：ls 根目录看不到其他项目；读其他项目被拒；写被拒；
   /skills/ 可读；glob/grep 根级搜索被引导。
2. 挂载项目 A 后：A 内读写正常、产物登记；B 读写搜索删除全部被拒。
3. 运行中 project_create（模拟 ctx.project 更新）后新项目即刻可写。
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agent_deep import _WorkspaceBackend
from app.services.capabilities import AgentContext


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="iso-test-"))
    fails: list[str] = []
    try:
        # 布景：两个项目 + 一个技能文件
        (root / "proj-a").mkdir()
        (root / "proj-a" / "a.md").write_text("A 内容", encoding="utf-8")
        (root / "proj-b").mkdir()
        (root / "proj-b" / "secret.md").write_text("B 机密", encoding="utf-8")
        skills = root / "skills" / "demo"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: demo\n---\nhello", encoding="utf-8")

        ctx = AgentContext(workspace_id="t", project=None)
        be = _WorkspaceBackend(str(root), ctx)

        # 1. 未挂载：根目录 ls 不出现任何项目
        ls = be.ls("/")
        names = {Path(e["path"]).name for e in (ls.entries or [])}
        if "proj-a" in names or "proj-b" in names:
            fails.append(f"未挂载时根目录可见项目: {sorted(names)}")
        if "skills" not in names:
            fails.append(f"未挂载时根目录应显示 skills: {sorted(names)}")

        # 2. 读其他项目被拒；读 skills 放行
        r = be.read("/proj-b/secret.md")
        if not r.error:
            fails.append("未挂载时读 proj-b 未被拒")
        r = be.read("/skills/demo/SKILL.md")
        if r.error:
            fails.append(f"skills 读取被误拒: {r.error}")

        # 3. 写被拒
        w = be.write("/proj-b/x.md", " hacked")
        if not w.error:
            fails.append("未挂载时写 proj-b 未被拒")
        if (root / "proj-b" / "x.md").exists():
            fails.append("越界写入竟然落盘了")

        # 4. 根级 glob/grep 被引导（无活动项目）
        g = be.glob("**/*.md")
        if not g.error:
            fails.append("无活动项目时根级 glob 未被引导")
        gr = be.grep("机密")
        if not gr.error:
            fails.append("无活动项目时根级 grep 未被引导")

        # 5. 挂载 proj-a：A 内读写正常且登记产物，B 全维度被拒
        ctx.project = "proj-a"
        ls = be.ls("/")
        names = {Path(e["path"]).name for e in (ls.entries or [])}
        if "proj-b" in names:
            fails.append(f"挂载 A 后根目录仍可见 proj-b: {sorted(names)}")
        r = be.read("/proj-a/a.md")
        if r.error:
            fails.append(f"挂载后读 A 失败: {r.error}")
        r = be.read("/proj-b/secret.md")
        if not r.error:
            fails.append("挂载 A 后读 proj-b 未被拒")
        w = be.write("/proj-a/out.md", "# 产出")
        if w.error:
            fails.append(f"挂载后写 A 失败: {w.error}")
        if not (root / "proj-a" / "out.md").exists():
            fails.append("A 内写入未落盘")
        if not any(a.get("path") == "out.md" for a in ctx.artifacts):
            fails.append(f"A 内写入未登记产物: {ctx.artifacts}")
        w = be.write("/proj-b/x.md", " hacked")
        if not w.error:
            fails.append("挂载 A 后写 proj-b 未被拒")
        d = be.delete("/proj-b/secret.md")
        if not d.error:
            fails.append("挂载 A 后删除 proj-b 文件未被拒")
        if (root / "proj-b" / "secret.md").read_text(encoding="utf-8") != "B 机密":
            fails.append("proj-b 文件被越界删除")
        g = be.glob("**/*.md")
        if g.error:
            fails.append(f"挂载后根级 glob 失败: {g.error}")
        else:
            paths = {e["path"] for e in (g.matches or g.entries or [])}
            if any("proj-b" in p for p in paths):
                fails.append(f"根级 glob 搜到了 proj-b: {paths}")
        gr = be.grep("机密")
        if not gr.error and any("proj-b" in str(m) for m in (gr.matches or [])):
            fails.append("根级 grep 搜到了 proj-b")

        # 6. 运行中创建新项目（模拟 project_create 设置 ctx.project）
        (root / "proj-c").mkdir()
        ctx.project = "proj-c"
        w = be.write("/proj-c/new.md", "ok")
        if w.error:
            fails.append(f"创建新项目后写入被误拒: {w.error}")
        w = be.write("/proj-a/steal.md", "x")
        if not w.error:
            fails.append("切换到 C 后写 proj-a 未被拒")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS: 项目边界成立——未挂载看不到也碰不到其他项目，挂载/创建后仅限活动项目 + /skills/ 只读")
    return 0


if __name__ == "__main__":
    sys.exit(main())
