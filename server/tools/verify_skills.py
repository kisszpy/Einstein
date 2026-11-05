"""技能动态安装冒烟测试（真实访问 GitHub 公开仓库）。

覆盖：市场浏览 → 预览（多技能仓库）→ 安装 → 幂等 → 文件读取 → 更新检查 →
卸载 → 路径穿越/非法地址防护。
用法（后端已启动）：.venv/Scripts/python.exe tools/verify_skills.py
"""

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOKEN = ""

REPO_URL = "https://github.com/anthropics/skills"
TARGET = "skills/algorithmic-art"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with _opener.open(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> None:
    global TOKEN
    code, login = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert code == 200, (code, login)
    TOKEN = login["token"]

    print("== 1. 技能市场")
    code, market = call("GET", "/api/skills/market?limit=5")
    assert code == 200, (code, market)
    print(f"   来源 {market['source']}，返回 {len(market['items'])} 条，限流兜底={market['rate_limited']}")
    for item in market["items"][:3]:
        print(f"   · {item['repo']} ★{item['stars']} 已装 {item['installed']} 个")

    print("== 2. 预览：多技能仓库")
    code, preview = call("POST", "/api/skills/install/preview", {"url": REPO_URL})
    assert code == 200, (code, preview)
    paths = [c["path"] for c in preview["candidates"]]
    assert TARGET in paths, paths
    print(f"   {preview['repo']['repo']}@{preview['repo']['commit_short']} 共 {len(paths)} 个技能，"
          f"含 {TARGET} ✓")

    print("== 3. 安装指定目录")
    code, res = call("POST", "/api/skills/install", {"url": REPO_URL, "path": TARGET})
    assert code == 200, (code, res)
    skill = res["skill"]
    assert res["created"] is True
    print(f"   {skill['name']}（{skill['id']}）commit {skill['commit'][:10]}，"
          f"文件 {len(skill['files'])} 个，提示词 {len(skill['system_prompt'])} 字")

    print("== 4. 幂等：重复安装同一目录")
    code, again = call("POST", "/api/skills/install", {"url": REPO_URL, "path": TARGET})
    assert code == 200 and again["created"] is False, (code, again)
    assert again["skill"]["id"] == skill["id"]
    print("   复用同一技能 id，不产生重复条目 ✓")

    print("== 5. 文件读取")
    code, files = call("GET", f"/api/skills/{skill['id']}/files")
    assert code == 200 and len(files["files"]) == len(skill["files"])
    name = files["files"][0]["path"]
    code, content = call(
        "GET", f"/api/skills/{skill['id']}/file?path={urllib.parse.quote(name)}"
    )
    assert code == 200 and content["is_text"] and content["content"]
    print(f"   {name} 读取 {len(content['content'])} 字符 ✓")

    code, denied = call(
        "GET", f"/api/skills/{skill['id']}/file?path=../../users.json"
    )
    assert code == 404, f"路径穿越应被拒，实际 {code}"
    print("   路径穿越 ../../users.json 被拒 ✓")

    print("== 6. 非法地址")
    code, bad = call("POST", "/api/skills/install/preview", {"url": "https://gitlab.com/foo/bar"})
    assert code == 400, (code, bad)
    code, bad = call("POST", "/api/skills/install/preview", {"url": "anthropics/definitely-not-exists-xyz"})
    assert code == 400, (code, bad)
    print(f"   非 GitHub 地址与不存在的仓库均被拒：{bad['detail'][:40]}…")

    print("== 7. 更新检查与卸载")
    code, upd = call("POST", f"/api/skills/{skill['id']}/update")
    assert code == 200, (code, upd)
    print(f"   检查更新：commit 变化={upd['changed']}")
    code, deleted = call("DELETE", f"/api/skills/{skill['id']}")
    assert code == 200 and deleted["ok"]
    code, listing = call("GET", "/api/skills")
    assert all(s["id"] != skill["id"] for s in listing)
    print("   已卸载，列表不再包含该技能 ✓")

    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
