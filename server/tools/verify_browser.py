"""内置浏览器后端能力的回归验证。

跑法：先起后端（uvicorn app.main:app --port 8000），然后
    ./.venv/Scripts/python.exe tools/verify_browser.py

覆盖三块：
- 产物预览：preview/file 的 MIME 与沙箱头、相对资源可访问、票据鉴权边界；
- 网页代理：base 注入与导航桥、XFO 不再限制、阅读模式、内网地址拒绝；
- 演示文稿：deck 保存 → 回读无损（sidecar）、外部 pptx 解析、页面增删生效。
"""

from __future__ import annotations

import glob
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
PROJECT = "browser-verify"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def request(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, str, str]:
    """curl 传输：本机沙箱会间歇性掐断 python 直连，curl 稳定。"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".hdr") as fh:
        hdr_path = fh.name
    args = ["curl", "-s", "--noproxy", "*", "-m", "90", "-X", method, "-D", hdr_path]
    if token:
        args += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body, ensure_ascii=False)]
    args += ["-w", "\n%{http_code}", BASE + path]
    proc = subprocess.run(args, capture_output=True)
    out = proc.stdout.decode("utf-8", "ignore")
    body_text, _, code = out.rpartition("\n")
    headers = Path(hdr_path).read_text("utf-8", "ignore")
    Path(hdr_path).unlink(missing_ok=True)
    try:
        status = int(code.strip())
    except ValueError:
        status = 0
    return status, headers, body_text


def login() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with opener.open(req, timeout=30) as resp:
        return json.load(resp)["token"]


def find_project_dir(project: str) -> Path | None:
    hits = glob.glob(str(Path(__file__).resolve().parent.parent / "data" / "projects" / "*" / project))
    return Path(hits[0]) if hits else None


def header_value(headers: str, name: str) -> str:
    for line in headers.splitlines():
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def main() -> int:
    token = login()
    print("登录成功")

    # ---------- 准备一个静态站点项目 ----------
    request("DELETE", f"/api/projects/{PROJECT}", token)
    status, _, body = request(
        "POST", "/api/projects", token,
        {"name": PROJECT, "template": "web", "description": "内置浏览器验证用"},
    )
    check("创建静态站点项目", status == 200 and "index.html" in body, f"{status} {body[:120]}")

    status, _, body = request("GET", "/api/browse/ticket", token)
    ticket = json.loads(body).get("ticket", "") if status == 200 else ""
    check("签发预览票据", status == 200 and len(ticket) > 20, f"{status} {body[:120]}")

    # ---------- 产物预览 ----------
    status, headers, body = request("GET", f"/api/projects/{PROJECT}/preview?path=index.html&ticket={ticket}")
    csp = header_value(headers, "content-security-policy")
    check("HTML 预览 200", status == 200 and "<h1" in body, f"{status} {body[:100]}")
    check("HTML 走沙箱隔离（CSP sandbox）", "sandbox" in csp and "allow-same-origin" not in csp, csp)
    check("HTML 类型正确", "text/html" in header_value(headers, "content-type"), header_value(headers, "content-type"))
    check("预览注入 base 指向文件端点",
          f'base href="/api/projects/{PROJECT}/file/{ticket}/' in body, body[:200])

    status, headers, body = request("GET", f"/api/projects/{PROJECT}/file/{ticket}/style.css")
    check("相对资源 style.css 可用", status == 200 and "text/css" in header_value(headers, "content-type"),
          f"{status} {header_value(headers, 'content-type')}")

    status, headers, body = request("GET", f"/api/projects/{PROJECT}/file/{ticket}/app.js")
    check("相对资源 app.js 可用", status == 200 and "javascript" in header_value(headers, "content-type"),
          f"{status} {header_value(headers, 'content-type')}")

    status, _, _ = request("GET", f"/api/projects/{PROJECT}/preview?path=index.html")
    check("无票据访问预览被拒", status == 401, str(status))

    status, _, _ = request("GET", f"/api/projects/{PROJECT}/file/not-a-real-ticket-0000/style.css")
    check("伪造票据被拒", status == 401, str(status))

    status, _, _ = request("GET", f"/api/kb?ticket={ticket}")
    check("票据不能跨端点使用", status == 401, str(status))

    status, _, body = request("GET", f"/api/projects/{PROJECT}/preview?path=..%2F..%2F.env&ticket={ticket}")
    check("预览拒绝路径穿越", status in (400, 404, 422) and "OPENAI" not in body, f"{status} {body[:80]}")

    # ---------- 网页代理 ----------
    status, headers, body = request("GET", f"/api/browse/web?url=https://example.com/&ticket={ticket}")
    check("网页代理 200", status == 200 and "<html" in body.lower(), f"{status} {body[:100]}")
    check("注入 base 保持相对链接", '<base href="https://example.com/' in body, body[:200])
    check("注入导航桥", "einstein-browser" in body, "bridge missing")
    check("不返回 X-Frame-Options", header_value(headers, "x-frame-options") == "",
          header_value(headers, "x-frame-options"))
    check("回传最终地址", header_value(headers, "x-final-url").startswith("https"), header_value(headers, "x-final-url"))

    status, _, body = request("GET", f"/api/browse/read?url=https://example.com/&ticket={ticket}")
    data = json.loads(body) if status == 200 else {}
    check("阅读模式取到正文", status == 200 and "Example Domain" in data.get("title", "")
          and data.get("chars", 0) > 50, f"{status} {body[:160]}")

    status, _, body = request("GET", f"/api/browse/web?url=http://127.0.0.1:8000/api/health&ticket={ticket}")
    check("内网地址被拒（SSRF）", status == 502 and "内网" in body, f"{status} {body[:120]}")

    status, _, _ = request("GET", "/api/browse/read?url=https://example.com/")
    check("阅读模式同样需要票据", status == 401, str(status))

    # ---------- 演示文稿：保存 / 回读 ----------
    deck_path = "deck/verify.pptx"
    payload = {
        "title": "浏览器验证",
        "subtitle": "内置浏览器 · PPT 编辑",
        "author": "Einstein",
        "theme": "ocean",
        "slides": [
            {"layout": "title", "title": "浏览器验证", "subtitle": "内置浏览器 · PPT 编辑"},
            {"layout": "bullets", "title": "三件事", "kicker": "Overview",
             "bullets": ["浏览产物", "浏览网页", "- 代理改写", "编辑 PPT"]},
            {"layout": "table", "title": "数据", "headers": ["项", "值"], "rows": [["页数", "3"], ["主题", "ocean"]]},
        ],
    }
    status, _, body = request("PUT", f"/api/projects/{PROJECT}/deck?path={deck_path}", token, {"deck": payload})
    saved = json.loads(body) if status == 200 else {}
    check("保存 deck 成功", status == 200 and len(saved.get("slides", [])) == 3, f"{status} {body[:160]}")
    check("保存后 source=sidecar 可无损编辑", saved.get("source") == "sidecar" and saved.get("editable") == "full",
          json.dumps({k: saved.get(k) for k in ("source", "editable")}, ensure_ascii=False))

    status, _, body = request("GET", f"/api/projects/{PROJECT}/deck?path={deck_path}", token)
    loaded = json.loads(body) if status == 200 else {}
    check("回读保持主题与要点", loaded.get("theme") == "ocean"
          and loaded["slides"][1].get("bullets") == payload["slides"][1]["bullets"],
          json.dumps(loaded.get("slides", [])[1:2], ensure_ascii=False)[:200])

    edited = dict(payload)
    edited["title"] = "改过的标题"
    edited["theme"] = "sunset"
    edited["slides"] = payload["slides"][:2] + [{"layout": "quote", "quote": "编辑后重新渲染", "subtitle": "Einstein"}]
    status, _, body = request("PUT", f"/api/projects/{PROJECT}/deck?path={deck_path}", token, {"deck": edited})
    after = json.loads(body) if status == 200 else {}
    check("改标题/换主题/换版式生效", after.get("title") == "改过的标题"
          and after.get("theme") == "sunset"
          and after["slides"][2].get("layout") == "quote", f"{status} {body[:160]}")

    status, headers, _ = request("GET", f"/api/projects/{PROJECT}/download?path={deck_path}", token)
    check("pptx 可下载（带文件名）", status == 200 and ".pptx" in header_value(headers, "content-disposition"),
          header_value(headers, "content-disposition"))

    # ---------- 外部 pptx 解析 ----------
    root = find_project_dir(PROJECT)
    if root is None:
        check("定位项目目录", False, "未找到 data/projects/*/browser-verify")
    else:
        from pptx import Presentation
        from pptx.util import Inches, Pt

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank = prs.slide_layouts[6]
        s1 = prs.slides.add_slide(blank)
        box = s1.shapes.add_textbox(Inches(1), Inches(2), Inches(10), Inches(2))
        box.text_frame.text = "外部风格封面"
        box.text_frame.paragraphs[0].runs[0].font.size = Pt(40)
        s2 = prs.slides.add_slide(blank)
        tb = s2.shapes.add_textbox(Inches(1), Inches(0.6), Inches(10), Inches(1))
        tb.text_frame.text = "外部要点页"
        tb.text_frame.paragraphs[0].runs[0].font.size = Pt(30)
        body_box = s2.shapes.add_textbox(Inches(1), Inches(2), Inches(10), Inches(3))
        tf = body_box.text_frame
        tf.text = "第一条"
        tf.add_paragraph().text = "第二条"
        s3 = prs.slides.add_slide(blank)
        tb3 = s3.shapes.add_textbox(Inches(1), Inches(0.6), Inches(10), Inches(1))
        tb3.text_frame.text = "谢谢观看"
        tb3.text_frame.paragraphs[0].runs[0].font.size = Pt(36)
        external = root / "deck" / "external.pptx"
        external.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(external))

        status, _, body = request("GET", f"/api/projects/{PROJECT}/deck?path=deck/external.pptx", token)
        parsed = json.loads(body) if status == 200 else {}
        layouts = [s.get("layout") for s in parsed.get("slides", [])]
        check("外部 pptx 可解析", status == 200 and len(parsed.get("slides", [])) == 3, f"{status} {body[:160]}")
        check("解析出封面/要点/结尾版式", layouts[:2] == ["title", "bullets"] and layouts[2] == "end", str(layouts))
        check("解析来源标记为 partial", parsed.get("editable") == "partial" and parsed.get("source") == "parsed",
              json.dumps({k: parsed.get(k) for k in ("source", "editable")}, ensure_ascii=False))
        check("解析出要点文本", "第一条" in json.dumps(parsed.get("slides", []), ensure_ascii=False),
              json.dumps(parsed.get("slides", [])[1:2], ensure_ascii=False)[:200])

    request("DELETE", f"/api/projects/{PROJECT}", token)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
