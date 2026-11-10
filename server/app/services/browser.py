"""内置浏览器的服务端支撑：网页代理改写、资产代理与阅读模式。

为什么需要服务端代理：绝大多数站点用 `X-Frame-Options` / CSP 的
`frame-ancestors` 禁止被 iframe 嵌入。代理把页面抓回来、去掉这些限制、
改写资源地址再吐给前端，就能在内置窗口里正常显示。

iframe 用的是 sandbox（无 allow-same-origin，防止外部脚本读主站存储），
因此页面里的 localStorage/sessionStorage/cookie 访问会直接抛异常——
很多站点脚本第一行就崩，SPA 整页白屏。导航桥里垫了一层内存 shim。

资源改写走 /api/browse/asset：
- 绕过防盗链（直连原站时 Referer/Origin 不对，图片 CSS 常 403）；
- 混合内容与证书问题也一并消掉。
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from app.services.capabilities import web


def nav_bridge(site_url: str) -> str:
    """导航桥 + storage shim。

    site_url 是原站地址：没有 <base> 的情况下，站内相对链接要靠它解析。
    """
    safe_url = escape(site_url, quote=True)
    return f"""<script data-einstein="bridge">
(function () {{
  var SITE = "{safe_url}";
  /* sandbox 里 storage/cookie 一访问就抛 SecurityError，脚本会整段崩掉。
     先垫一层内存版，让页面脚本至少能跑起来（会话内有效，不落盘）。 */
  var make = function () {{
    var store = {{}};
    var api = {{
      getItem: function (k) {{ return Object.prototype.hasOwnProperty.call(store, String(k)) ? store[String(k)] : null; }},
      setItem: function (k, v) {{ store[String(k)] = String(v); }},
      removeItem: function (k) {{ delete store[String(k)]; }},
      clear: function () {{ store = {{}}; }},
      key: function (i) {{ return Object.keys(store)[i] || null; }},
    }};
    Object.defineProperty(api, "length", {{ get: function () {{ return Object.keys(store).length; }} }});
    return api;
  }};
  try {{ Object.defineProperty(window, "localStorage", {{ value: make(), configurable: false }}); }} catch (e) {{}}
  try {{ Object.defineProperty(window, "sessionStorage", {{ value: make(), configurable: false }}); }} catch (e) {{}}
  try {{
    Object.defineProperty(document, "cookie", {{ get: function () {{ return ""; }}, set: function () {{}}, configurable: false }});
  }} catch (e) {{}}

  var post = function (msg) {{ try {{ parent.postMessage(msg, "*"); }} catch (e) {{}} }};
  var meta = function () {{
    post({{ source: "einstein-browser", type: "ready", url: SITE, title: document.title || "" }});
  }};
  if (document.readyState === "complete" || document.readyState === "interactive") {{ setTimeout(meta, 0); }}
  else {{ document.addEventListener("DOMContentLoaded", meta); }}
  document.addEventListener("click", function (e) {{
    var el = e.target;
    while (el && el.tagName !== "A") {{ el = el.parentElement; }}
    if (!el) {{ return; }}
    var href = el.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") {{ return; }}
    var abs;
    try {{ abs = new URL(href, SITE).href; }} catch (err) {{ return; }}
    if (!/^https?:/i.test(abs)) {{ return; }}
    e.preventDefault();
    e.stopPropagation();
    post({{ source: "einstein-browser", type: "navigate", url: abs }});
  }}, true);
  document.addEventListener("submit", function (e) {{
    e.preventDefault();
    post({{ source: "einstein-browser", type: "notice", message: "内置浏览器不提交表单，可切换到「阅读模式」或系统浏览器" }});
  }}, true);
  window.open = function () {{ return null; }};
}})();
</script>"""

_CSP_META = re.compile(
    r"<meta[^>]+http-equiv\s*=\s*[\"']?content-security-policy[\"']?[^>]*>", re.I
)

_SKIP_SCHEME = re.compile(r"^(data:|blob:|javascript:|mailto:|tel:|#)", re.I)
_ASSET_TAGS = {
    "script": ["src"],
    "img": ["src", "srcset"],
    "source": ["src", "srcset"],
    "video": ["src", "poster"],
    "audio": ["src"],
    "track": ["src"],
    "embed": ["src"],
    "object": ["data"],
    "input": ["src"],
}
_ASSET_RELS = ("stylesheet", "icon", "shortcut icon", "apple-touch-icon", "manifest", "preload")


def asset_url(url: str, ticket: str) -> str:
    """把任意 http(s) 资源地址变成走代理的资产路径。"""
    return f"/api/browse/asset?url={quote(url, safe='')}&ticket={quote(ticket, safe='')}"


def page_url(url: str, ticket: str) -> str:
    """嵌套 iframe 的 src 指到代理页面（要继续改写 HTML）。"""
    return f"/api/browse/web?url={quote(url, safe='')}&ticket={quote(ticket, safe='')}"


def _resolve(raw: str, base_url: str) -> str | None:
    raw = (raw or "").strip()
    if not raw or _SKIP_SCHEME.match(raw) or raw.lower().startswith("/api/"):
        return None
    try:
        abs_url = urljoin(base_url, raw)
    except ValueError:
        return None
    return abs_url if abs_url.startswith(("http://", "https://")) else None


def _srcset(text: str, base_url: str, ticket: str) -> str:
    parts = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        chunks = item.split()
        abs_url = _resolve(chunks[0], base_url)
        if abs_url:
            chunks[0] = asset_url(abs_url, ticket)
        parts.append(" ".join(chunks))
    return ", ".join(parts)


def rewrite_css(css: str, base_url: str, ticket: str) -> str:
    """CSS 里的 url() 与 @import 一并代理——CSS 背景图是防盗链重灾区。"""

    def sub_url(m: re.Match) -> str:
        raw = m.group(3).strip()
        abs_url = _resolve(raw, base_url)
        if not abs_url:
            return m.group(0)
        return f"{m.group(1)}{asset_url(abs_url, ticket)}{m.group(2)}{m.group(2)}{m.group(4)}"

    css = re.sub(r"(url\(\s*)(['\"]?)([^'\"\s)]+)\2(\s*\))", sub_url, css, flags=re.I)

    def sub_import(m: re.Match) -> str:
        abs_url = _resolve(m.group(3), base_url)
        if not abs_url:
            return m.group(0)
        return f'{m.group(1)}{asset_url(abs_url, ticket)}{m.group(2)}{m.group(4)}'

    css = re.sub(r'(@import\s+)(["\'])([^"\']+)\2(\s*[^;]*;)', sub_import, css, flags=re.I)
    return css


def rewrite_assets(html: str, final_url: str, ticket: str) -> str:
    """把 HTML 里的静态资源指到 /api/browse/asset，嵌套 iframe 指回代理页面。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        name = (tag.name or "").lower()

        if name == "link":
            rel = " ".join(tag.get("rel") or []).lower()
            if any(r in rel for r in _ASSET_RELS):
                href = tag.get("href")
                abs_url = _resolve(href, final_url)
                if abs_url:
                    tag["href"] = asset_url(abs_url, ticket)
            continue

        if name == "iframe":
            src = tag.get("src")
            abs_url = _resolve(src, final_url)
            if abs_url:
                tag["src"] = page_url(abs_url, ticket)
                # 嵌套页面自己有导航桥，去掉外层 sandbox 属性限制无意义，保留原样
            continue

        if name == "style" and tag.string:
            tag.string.replace_with(rewrite_css(str(tag.string), final_url, ticket))
            continue

        attrs = _ASSET_TAGS.get(name)
        if attrs:
            for attr in attrs:
                val = tag.get(attr)
                if not val:
                    continue
                if attr == "srcset":
                    tag[attr] = _srcset(val, final_url, ticket)
                else:
                    abs_url = _resolve(val, final_url)
                    if abs_url:
                        tag[attr] = asset_url(abs_url, ticket)

        style = tag.get("style")
        if style:
            tag["style"] = rewrite_css(style, final_url, ticket)

    return str(soup)


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


def rewrite_html(html: str, final_url: str, ticket: str = "") -> str:
    """去 CSP meta、注入导航桥，资源改写走资产代理。

    注意：网页代理场景**不能**插 `<base href="原站">` —— 它会把我们改写
    出的根相对代理路径（/api/browse/asset?...）重新解析回原站域名，图片
    CSS 全部 404。静态资源已全部代理，链接解析交给导航桥里的 SITE 常量。
    """
    html = _CSP_META.sub("", html)
    bridge = nav_bridge(final_url)
    head = re.search(r"<head[^>]*>", html, re.I)
    if head:
        html = html[: head.end()] + bridge + html[head.end() :]
    else:
        html_tag = re.search(r"<html[^>]*>", html, re.I)
        if html_tag:
            html = html[: html_tag.end()] + "<head>" + bridge + "</head>" + html[html_tag.end() :]
        else:
            html = bridge + html
    if ticket:
        html = rewrite_assets(html, final_url, ticket)
    return html


def is_html(ctype: str) -> bool:
    if not ctype or "html" in ctype:
        return True
    return "xml" in ctype and "image" not in ctype


def effective_type(ctype: str) -> str:
    base = (ctype or "text/html").split(";")[0].strip() or "text/html"
    if base in ("application/xhtml+xml", "application/xml", "text/xml"):
        return "text/html"
    return base


def frame_payload(url: str, ticket: str = "") -> tuple[bytes, str, str]:
    """抓取并改写：返回 (body, content_type, final_url)。"""
    raw, final_url, ctype = web.fetch_raw(url)
    if is_html(ctype):
        html = rewrite_html(web.decode_body(raw, ctype), final_url, ticket)
        return html.encode("utf-8"), "text/html; charset=utf-8", final_url
    if "css" in ctype:
        css = rewrite_css(web.decode_body(raw, ctype), final_url, ticket)
        return css.encode("utf-8"), "text/css; charset=utf-8", final_url
    return raw, effective_type(ctype), final_url


def asset_payload(url: str, ticket: str = "") -> tuple[bytes, str, str]:
    """抓单个资源：CSS 文本内的引用也一并代理。"""
    raw, final_url, ctype = web.fetch_raw(url)
    etype = effective_type(ctype)
    if etype == "text/css":
        css = rewrite_css(web.decode_body(raw, ctype), final_url, ticket)
        return css.encode("utf-8"), "text/css; charset=utf-8", final_url
    return raw[: web.MAX_BYTES], etype or "application/octet-stream", final_url


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
