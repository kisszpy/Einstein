"""内置浏览器的服务端支撑：网页代理改写与阅读模式。

为什么需要服务端代理：绝大多数站点用 `X-Frame-Options` / CSP 的
`frame-ancestors` 禁止被 iframe 嵌入。代理把页面抓回来、去掉这些限制、
补上 `<base href>`（否则相对路径资源全挂）再吐给前端，就能在内置窗口里
正常显示。

改写只做「让页面能显示」的最小改动，不注入业务脚本：
- 去掉 CSP meta（否则内联脚本与代理页面会被拦）；
- 插 `<base>` 保持相对链接与资源指向原站；
- 插一段导航桥，把站内链接点击上报给父窗口——否则 iframe 直接跳原站
  会再次被 frame 限制挡住，用户看到白屏。
"""

from __future__ import annotations

import re
from html import escape

from app.services.capabilities import web

NAV_BRIDGE = """<script data-einstein="bridge">
(function () {
  var post = function (msg) { try { parent.postMessage(msg, "*"); } catch (e) {} };
  var meta = function () {
    post({ source: "einstein-browser", type: "ready", url: location.href, title: document.title || "" });
  };
  if (document.readyState === "complete" || document.readyState === "interactive") { setTimeout(meta, 0); }
  else { document.addEventListener("DOMContentLoaded", meta); }
  document.addEventListener("click", function (e) {
    var el = e.target;
    while (el && el.tagName !== "A") { el = el.parentElement; }
    if (!el) { return; }
    var href = el.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") { return; }
    var abs;
    try { abs = new URL(href, document.baseURI).href; } catch (err) { return; }
    if (!/^https?:/i.test(abs)) { return; }
    e.preventDefault();
    e.stopPropagation();
    post({ source: "einstein-browser", type: "navigate", url: abs });
  }, true);
  document.addEventListener("submit", function (e) {
    e.preventDefault();
    post({ source: "einstein-browser", type: "notice", message: "内置浏览器不提交表单，可切换到「阅读模式」或系统浏览器" });
  }, true);
  window.open = function () { return null; };
})();
</script>"""

_CSP_META = re.compile(
    r"<meta[^>]+http-equiv\s*=\s*[\"']?content-security-policy[\"']?[^>]*>", re.I
)


def inject_base(html: str, href: str) -> str:
    """只插 `<base>`：产物站点靠它把相对路径指回文件端点。"""
    tag = f'<base href="{escape(href, quote=True)}">'
    head = re.search(r"<head[^>]*>", html, re.I)
    if head:
        return html[: head.end()] + tag + html[head.end() :]
    html_tag = re.search(r"<html[^>]*>", html, re.I)
    if html_tag:
        return html[: html_tag.end()] + "<head>" + tag + "</head>" + html[html_tag.end() :]
    return tag + html


def rewrite_html(html: str, final_url: str) -> str:
    """注入 base 与导航桥。已经是完整文档的页面直接改，片段也兜住。"""
    html = _CSP_META.sub("", html)
    tag = f'<base href="{escape(final_url, quote=True)}">{NAV_BRIDGE}'
    head = re.search(r"<head[^>]*>", html, re.I)
    if head:
        return html[: head.end()] + tag + html[head.end() :]
    html_tag = re.search(r"<html[^>]*>", html, re.I)
    if html_tag:
        return html[: html_tag.end()] + "<head>" + tag + "</head>" + html[html_tag.end() :]
    return tag + html


def is_html(ctype: str) -> bool:
    if not ctype or "html" in ctype:
        return True
    return "xml" in ctype and "image" not in ctype


def effective_type(ctype: str) -> str:
    base = (ctype or "text/html").split(";")[0].strip() or "text/html"
    if base in ("application/xhtml+xml", "application/xml", "text/xml"):
        return "text/html"
    return base


def frame_payload(url: str) -> tuple[bytes, str, str]:
    """抓取并改写：返回 (body, content_type, final_url)。"""
    raw, final_url, ctype = web.fetch_raw(url)
    if is_html(ctype):
        html = rewrite_html(web.decode_body(raw, ctype), final_url)
        return html.encode("utf-8"), "text/html; charset=utf-8", final_url
    return raw, effective_type(ctype), final_url


def error_page(url: str, message: str) -> str:
    """代理失败时给 iframe 的占位页——比浏览器的裸错误更容易读懂。"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>无法打开</title><style>
html,body{{height:100%;margin:0}}
body{{display:grid;place-items:center;background:#0b1220;color:#e2e8f0;
font:14px/1.7 "Microsoft YaHei",system-ui,sans-serif}}
.card{{max-width:30rem;padding:28px 30px;border:1px solid #1e293b;border-radius:14px;background:#111c2e}}
h1{{margin:0 0 10px;font-size:15px;font-weight:600;color:#f87171}}
p{{margin:0 0 6px;color:#94a3b8;word-break:break-all}}
code{{color:#7dd3fc;font-size:12px}}
</style></head><body><div class="card">
<h1>打不开这个页面</h1><p><code>{escape(url)}</code></p><p>{escape(message)}</p>
<p style="margin-top:12px">可以试试「阅读模式」，或改用系统浏览器打开。</p>
</div></body></html>"""
