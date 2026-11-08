"""S2.7 技能 × 智能体 绑定验证。

覆盖：
1. 发布器：DB 技能 → <workspace>/skills/<slug>/SKILL.md，frontmatter 合规
2. 热加载：停用后同步，目录即消失；恢复后回来
3. pinned_slugs：无效/跨工作区技能被静默丢弃
4. 端到端：POST /api/agent/run 带 skill_ids，服务端日志打出 skills= 且目录已发布
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # server/ 目录，供 import app
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_auth import bearer, call  # noqa: E402

from app.services.skill_publisher import (  # noqa: E402
    export_manifest,
    pinned_slugs,
    slug_of,
    skills_dir,
    sync_skills,
)
from app.services.skills import get_skill_store

BASE_WS = "default"
OTHER_WS = "verify-other-ws"


def login():
    code, resp = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert code == 200, (code, resp)
    return bearer(resp["token"])


def main() -> None:
    print("== 1. 发布器：SKILL.md 合规")
    names = sync_skills(BASE_WS)
    assert names, "至少要发布出一个技能"
    root = skills_dir(BASE_WS)
    md = (root / names[0] / "SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---\nname: "), md[:60]
    slug_line = md.split("\n")[1]
    assert slug_line == f"name: {names[0]}", slug_line
    assert "\ndescription: " in md
    # 中文原名必须出现在 description（规范不收中文，但人类要看得懂）
    print(f"   发布 {len(names)} 个技能，frontmatter 合规，示例 description:",
          md.split("description: ", 1)[1].split("\n", 1)[0][:40], "…")

    print("== 2. 热加载：停用即消失")
    store = get_skill_store()
    probe = store.add(
        name="验证-热加载", description="verify hot reload",
        system_prompt="验证用正文热加载标记 HOTRELOAD123",
        tools=[], workspace_id=BASE_WS,
    )
    store.save()
    names2 = sync_skills(BASE_WS)
    slug = slug_of(probe)
    assert slug in names2, (slug, names2)
    assert (root / slug / "SKILL.md").exists()
    probe.enabled = False
    store.save()
    names3 = sync_skills(BASE_WS)
    assert slug not in names3 and not (root / slug).exists(), "停用后目录应被清掉"
    probe.enabled = True
    store.save()
    sync_skills(BASE_WS)
    assert (root / slug / "SKILL.md").exists()
    print("   停用→目录消失，恢复→目录回来 ✓")

    print("== 3. pinned_slugs：无效与跨工作区丢弃")
    store2 = get_skill_store()
    other = store2.add(
        name="验证-他区", description="other ws",
        system_prompt="OTHER_WS_ONLY", tools=[], workspace_id=OTHER_WS,
    )
    store2.save()
    pinned = pinned_slugs(BASE_WS, [probe.id, other.id, "nonexistent"])
    assert slug_of(probe) in pinned
    assert slug_of(other) not in pinned, "跨工作区技能不得出现在 pinned"
    assert "skill-nonexistent" not in pinned
    print(f"   pinned = {pinned}（他区与不存在项已被过滤）✓")

    print("== 4. 端到端：/api/agent/run 带 skill_ids")
    auth = login()
    code, created = call(
        "POST", "/api/skills",
        {
            "name": "验证-端到端",
            "description": "verify e2e pinning",
            "system_prompt": "端到端验证技能 E2ESKILL456：回复里必须包含字样 PINNED_OK。",
            "tools": [],
        },
        auth,
    )
    assert code == 200, (code, created)
    sid = created["id"]

    # 用 urllib 直接读 SSE：只要 meta 帧确认服务端受理即可，机制由日志断言
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/agent/run",
        data=json.dumps({"task": "只回复一个字：好", "skill_ids": [sid]}).encode(),
        headers={"Content-Type": "application/json", **auth},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            meta_seen = False
            finished = False
            err = ""
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("event:") and "meta" in line:
                    meta_seen = True
                if line.startswith("data:") and '"error"' in line:
                    err = line[:300]
                if line.startswith("event:") and "done" in line:
                    finished = True
                    break
        assert meta_seen, "未收到 meta 帧"
        assert finished, f"流未正常完成：{err or '无 done 帧'}"
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"SSE 请求失败: {exc}")

    log = Path(__file__).resolve().parents[1].joinpath("uvicorn_8000.log").read_text(encoding="utf-8")
    slug2 = f"skill-{sid}"
    assert f"skills={slug2}" in log, "服务端未记录技能装载"
    assert (root / slug2 / "SKILL.md").exists(), "任务开始时应已发布该技能目录"
    call("DELETE", f"/api/skills/{sid}", headers=auth)
    sync_skills(BASE_WS)
    assert not (root / slug2).exists()
    print("   run 带 skill_ids → 审计打印 + 目录发布 + 清理回收 ✓")

    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
