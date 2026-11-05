"""联网能力：网页检索与正文抓取。

搜索走 DuckDuckGo 的 HTML 端点（无需密钥），失败时回退 Bing；
抓取用 BeautifulSoup 去导航/脚本/侧栏后取正文。两层防护：
只允许 http(s)、解析 DNS 后拒绝内网/回环/链路本地地址（防 SSRF），
响应体与文本长度都有硬上限，避免模型上下文被单个页面撑爆。
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

MAX_BYTES = 3 * 1024 * 1024
FETCH_TIMEOUT = 20
SEARCH_TIMEOUT = 15
CACHE_TTL = 600

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
# DuckDuckGo 反爬后一段时间内直接走 Bing，省掉必然失败的一轮
_ddg_block_until = 0.0
DDG_BLOCK_SEC = 600


class WebError(Exception):
    """联网失败的可预期原因，直接回给模型。"""


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _cache_put(key: str, value: dict) -> None:
    with _cache_lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = (time.time(), value)


def _open(url: str, timeout: int, data: bytes | None = None):
    """依次尝试：直连(验签) → 系统代理(验签) → 直连/代理(不验签)。

    沙箱网络存在 TLS 拦截，系统证书库可能没有拦截方证书；最后一轮
    关闭验证兜底（只读公开网页，风险可接受）。
    """
    import ssl

    insecure_ctx = ssl.create_default_context()
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE

    attempts = [
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
        urllib.request.build_opener(),
        urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=insecure_ctx)),
        urllib.request.build_opener(urllib.request.HTTPSHandler(context=insecure_ctx)),
    ]
    last: Exception | None = None
    for opener in attempts:
        try:
            req = urllib.request.Request(url, data=data, headers=HEADERS)
            return opener.open(req, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
    raise WebError(f"连接失败：{last}")


def _assert_public(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebError("只支持 http/https 地址")
    host = parsed.hostname or ""
    if not host:
        raise WebError("地址缺少主机名")
    if host.lower() in ("localhost", "localhost.localdomain"):
        raise WebError("不允许访问本机地址")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebError(f"域名解析失败：{host}") from exc
    # 198.18.0.0/15 是代理工具 fake-ip DNS 的惯用段：本地解析是假地址，
    # 真实连接由代理出站，不构成内网访问，放行；其余保留/内网地址一律拒绝。
    fake_ip_net = ipaddress.ip_network("198.18.0.0/15")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip in fake_ip_net:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise WebError("不允许访问内网地址")


def _decode_ddg_link(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


# ---------- 搜索 ----------

def _search_ddg(query: str, limit: int) -> list[dict]:
    global _ddg_block_until
    if time.time() < _ddg_block_until:
        raise WebError("DuckDuckGo 处于反爬冷却期")
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    with _open(url, SEARCH_TIMEOUT) as resp:
        html = resp.read().decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for block in soup.select(".result"):
        a = block.select_one("a.result__a")
        if not a:
            continue
        snippet_el = block.select_one(".result__snippet")
        out.append(
            {
                "title": a.get_text(" ", strip=True),
                "url": _decode_ddg_link(a.get("href") or ""),
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            }
        )
        if len(out) >= limit:
            break
    if not out and ("anomaly" in html.lower() or "unusual traffic" in html.lower()):
        # 反爬校验页：别让上层误以为是「没搜到」，直接换引擎
        _ddg_block_until = time.time() + DDG_BLOCK_SEC
        raise WebError("DuckDuckGo 触发反爬校验")
    return out


def _decode_bing_link(href: str) -> str:
    """Bing HTML 结果里是 ck/a 跳转壳，真实地址在 u 参数的 base64 里。"""
    if "bing.com/ck/a" not in href:
        return href
    params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    u = (params.get("u") or [""])[0]
    if not u.startswith("a1"):
        return href
    token = u[2:] + "=" * (-len(u[2:]) % 4)
    try:
        return base64.urlsafe_b64decode(token).decode("utf-8", "ignore") or href
    except Exception:  # noqa: BLE001 — 解不出就保留原链接
        return href


def _search_bing_html(query: str, limit: int) -> list[dict]:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=zh-CN"
    with _open(url, SEARCH_TIMEOUT) as resp:
        html = resp.read().decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        sn = li.select_one(".b_caption p") or li.select_one("p")
        out.append(
            {
                "title": re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
                "url": _decode_bing_link(a["href"]),
                "snippet": sn.get_text(" ", strip=True) if sn else "",
            }
        )
        if len(out) >= limit:
            break
    return out


def _search_bing(query: str, limit: int) -> list[dict]:
    """优先走 RSS 端点：链接是真实地址、自带摘要，比抓 HTML 稳得多。"""
    url = "https://www.bing.com/search?format=rss&count={}&q={}".format(
        max(10, limit), urllib.parse.quote(query)
    )
    with _open(url, SEARCH_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    soup = BeautifulSoup(raw, "xml")
    out: list[dict] = []
    for item in soup.find_all("item"):
        link = item.link.get_text(strip=True) if item.link else ""
        if not link.startswith("http"):
            continue
        title = item.title.get_text(" ", strip=True) if item.title else link
        desc = item.description.get_text(" ", strip=True) if item.description else ""
        out.append(
            {
                "title": re.sub(r"\s+", " ", title) or link,
                "url": link,
                "snippet": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", desc))[:300],
            }
        )
        if len(out) >= limit:
            break
    if out:
        return out
    return _search_bing_html(query, limit)


def _search_so360(query: str, limit: int) -> list[dict]:
    url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
    with _open(url, SEARCH_TIMEOUT) as resp:
        html = resp.read().decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for li in soup.select("li.res-list"):
        a = li.select_one("h3 a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if href.startswith("/"):
            href = "https://www.so.com" + href
        if not href.startswith("http"):
            continue
        desc = li.select_one(".res-desc") or li.select_one(".res-comm-con")
        out.append(
            {
                "title": re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
                "url": href,
                "snippet": re.sub(r"\s+", " ", desc.get_text(" ", strip=True))[:300] if desc else "",
            }
        )
        if len(out) >= limit:
            break
    if not out:
        raise WebError("360 搜索无结果（可能触发反爬）")
    return out


def _search_sogou(query: str, limit: int) -> list[dict]:
    url = "https://www.sogou.com/web?query=" + urllib.parse.quote(query)
    with _open(url, SEARCH_TIMEOUT) as resp:
        html = resp.read().decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for h3 in soup.select("h3"):
        a = h3.find("a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if href.startswith("/link"):
            href = "https://www.sogou.com" + href
        if not href.startswith("http"):
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        box = h3.parent.parent if h3.parent and h3.parent.parent else h3
        full = re.sub(r"\s+", " ", box.get_text(" ", strip=True))
        snippet = full.replace(title, "", 1).strip()[:300]
        out.append({"title": title, "url": href, "snippet": snippet})
        if len(out) >= limit:
            break
    if not out:
        raise WebError("搜狗无结果（可能触发反爬）")
    return out


STOP_CHARS = set("的了是在和与及或对把被就都也还很更最我你他她它们这那个么怎什")


def _tokens(text: str) -> set[str]:
    """粗粒度关键词集合：中文按 2-gram，英文按词。只用于「像不像同一个话题」。"""
    cleaned = re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】<>《》|/]+", " ", (text or "").lower())
    out: set[str] = set()
    for chunk in cleaned.split():
        if chunk.isascii():
            if len(chunk) >= 2:
                out.add(chunk)
        else:
            for i in range(len(chunk) - 1):
                gram = chunk[i : i + 2]
                if not any(ch in STOP_CHARS for ch in gram):
                    out.add(gram)
    return out


def _relevant(query: str, rows: list[dict]) -> list[dict]:
    """过滤与查询词毫无交集的条目——反爬降级时搜索引擎会塞无关页面。"""
    qt = _tokens(query)
    if not qt:
        return rows
    return [r for r in rows if qt & _tokens(f"{r.get('title', '')} {r.get('snippet', '')}")]


ENGINES: tuple[tuple[str, object], ...] = (
    ("duckduckgo", _search_ddg),
    ("360", _search_so360),
    ("sogou", _search_sogou),
    ("bing", _search_bing),
    ("bing-html", _search_bing_html),
)


def web_search(query: str, max_results: int = 6) -> str:
    """返回可直接喂给模型的 JSON 字符串。

    多个引擎依次尝试：任何一家返回的结果都要先过相关性校验，
    零重叠视为被反爬页顶替，换下一家。全失败就返回空结果 + 原因，
    宁可不给资料，也不要拿垃圾页面污染模型上下文。
    """
    q = (query or "").strip()
    if not q:
        raise WebError("查询词为空")
    limit = max(1, min(int(max_results or 6), 12))
    key = f"search::{q}::{limit}"
    cached = _cache_get(key)
    if cached:
        return json.dumps({**cached, "cached": True}, ensure_ascii=False, indent=2)

    errors: list[str] = []
    for name, fn in ENGINES:
        try:
            results = fn(q, limit)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 — 换下一个引擎
            errors.append(f"{name}: {type(exc).__name__} {exc}")
            continue
        if not results:
            errors.append(f"{name}: 无结果")
            continue
        good = _relevant(q, results)
        if good:
            payload = {"query": q, "engine": name, "results": good[:limit]}
            _cache_put(key, payload)
            return json.dumps(payload, ensure_ascii=False, indent=2)
        errors.append(f"{name}: 结果与查询不相关")

    return json.dumps(
        {"query": q, "results": [], "error": "搜索失败，" + "；".join(errors)},
        ensure_ascii=False,
        indent=2,
    )


# ---------- 抓取 ----------

_DROP_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg")


def _extract(html: str, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or url
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body or soup
    text = node.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return title, text


def web_fetch(url: str, max_chars: int = 6000) -> str:
    target = (url or "").strip()
    if not target:
        raise WebError("地址为空")
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    _assert_public(target)
    limit = max(500, min(int(max_chars or 6000), 20000))
    key = f"fetch::{target}::{limit}"
    cached = _cache_get(key)
    if cached:
        return json.dumps({**cached, "cached": True}, ensure_ascii=False, indent=2)

    try:
        with _open(target, FETCH_TIMEOUT) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(MAX_BYTES + 1)
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        raise WebError(f"页面返回 {exc.code}") from exc

    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    if "application/pdf" in ctype:
        return json.dumps(
            {"url": final_url, "content_type": ctype, "text": "（PDF 文件，暂不支持正文抽取，请改用网页版）"},
            ensure_ascii=False,
            indent=2,
        )
    if ctype and not any(x in ctype for x in ("html", "text", "json", "xml")):
        return json.dumps(
            {"url": final_url, "content_type": ctype, "text": "（非文本内容，未解析）"},
            ensure_ascii=False,
            indent=2,
        )

    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    try:
        html = raw.decode(charset, "ignore")
    except LookupError:
        html = raw.decode("utf-8", "ignore")

    if "json" in ctype:
        payload = {"url": final_url, "content_type": ctype, "text": html[:limit]}
        _cache_put(key, payload)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    title, text = _extract(html, final_url)
    body = text[:limit]
    payload = {
        "url": final_url,
        "title": title,
        "chars": len(text),
        "truncated": len(text) > limit,
        "text": body,
    }
    _cache_put(key, payload)
    return json.dumps(payload, ensure_ascii=False, indent=2)
