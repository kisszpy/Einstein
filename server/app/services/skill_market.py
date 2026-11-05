"""技能市场聚合。

两条来源：
1. GitHub topic 搜索（默认 `agent-skills`）——开箱即用的公开市场，按 star 排序；
2. 自建 registry —— 一个 JSON 索引，适合内网/团队私有技能集市，通过
   `APP_SKILL_MARKET_REGISTRY_URLS` 配置（逗号分隔）。

结果带短时缓存，避免每次翻页都打 GitHub 的搜索配额（匿名 10 次/分钟）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse

from app.core.config import get_settings
from app.services.skill_installer import SkillInstallError, github_json

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300
_lock = threading.Lock()

# 匿名 GitHub 接口额度很低（搜索 10 次/分钟），限流时用内置精选兜底，
# 保证市场页永远有内容可点；不伪造星标与更新时间，只保留仓库与描述。
CURATED = [
    {
        "repo": "anthropics/skills",
        "name": "Anthropic Agent Skills",
        "description": "Anthropic 官方 Agent Skills 仓库，覆盖文档、设计、工程等通用技能。",
    },
    {
        "repo": "addyosmani/agent-skills",
        "name": "Agent Skills by Addy Osmani",
        "description": "面向 AI 编码代理的生产级工程技能集合。",
    },
    {
        "repo": "DietrichGebert/ponytail",
        "name": "Ponytail",
        "description": "让代理像资深工程师一样做减法：先质疑需求，再写代码。",
    },
    {
        "repo": "paperclipai/paperclip",
        "name": "Paperclip",
        "description": "代理工作流与技能组织示例仓库。",
    },
]


def _cache_get(key: str) -> dict | None:
    with _lock:
        hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _cache_put(key: str, value: dict) -> None:
    with _lock:
        _CACHE[key] = (time.monotonic(), value)


def topics() -> list[str]:
    raw = get_settings().skill_market_topics or "agent-skills"
    return [t.strip() for t in raw.split(",") if t.strip()]


def registry_urls() -> list[str]:
    raw = get_settings().skill_market_registry_urls or ""
    return [u.strip() for u in raw.split(",") if u.strip()]


def _normalize_github(item: dict) -> dict:
    repo = str(item.get("full_name") or "")
    return {
        "key": f"github:{repo}",
        "source": "github",
        "name": str(item.get("name") or repo),
        "full_name": repo,
        "repo": repo,
        "description": str(item.get("description") or ""),
        "author": str((item.get("owner") or {}).get("login") or repo.split("/")[0]),
        "stars": int(item.get("stargazers_count") or 0),
        "topics": list(item.get("topics") or [])[:8],
        "license": str((item.get("license") or {}).get("spdx_id") or ""),
        "url": str(item.get("html_url") or f"https://github.com/{repo}"),
        "install_url": str(item.get("html_url") or f"https://github.com/{repo}"),
        "pushed_at": str(item.get("pushed_at") or ""),
    }


def _curated_items(query: str) -> list[dict]:
    rows = []
    for entry in CURATED:
        row = {
            "key": f"curated:{entry['repo']}",
            "source": "curated",
            "name": entry["name"],
            "full_name": entry["repo"],
            "repo": entry["repo"],
            "description": entry["description"],
            "author": entry["repo"].split("/")[0],
            "stars": 0,
            "topics": [],
            "license": "",
            "url": f"https://github.com/{entry['repo']}",
            "install_url": f"https://github.com/{entry['repo']}",
            "pushed_at": "",
            "fallback": True,
        }
        if query.strip():
            haystack = f"{row['name']} {row['description']} {row['repo']}".lower()
            if query.strip().lower() not in haystack:
                continue
        rows.append(row)
    return rows


def search_github(query: str, limit: int = 20) -> dict:
    terms = [f"topic:{t}" for t in topics()]
    q = " ".join(terms)
    if query.strip():
        q = f"{q} {query.strip()}"
    url = (
        "https://api.github.com/search/repositories?"
        f"q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page={max(1, min(limit, 50))}"
    )
    data = github_json(url)
    items = [_normalize_github(row) for row in data.get("items", [])]
    return {
        "source": "github",
        "query": query,
        "topics": topics(),
        "total": int(data.get("total_count") or len(items)),
        "items": items,
    }


def _normalize_registry(item: dict, source_name: str) -> dict:
    repo = str(item.get("repo") or "")
    path = str(item.get("path") or "").strip("/")
    ref = str(item.get("ref") or "")
    url = str(item.get("url") or "")
    if not url and repo:
        url = f"https://github.com/{repo}"
        if path:
            url += f"/tree/{ref or 'main'}/{path}"
        elif ref:
            url += f"/tree/{ref}"
    return {
        "key": f"registry:{source_name}:{item.get('id') or repo or item.get('name')}",
        "source": "registry",
        "name": str(item.get("name") or repo or "未命名技能"),
        "full_name": repo,
        "repo": repo,
        "description": str(item.get("description") or ""),
        "author": str(item.get("author") or source_name),
        "stars": int(item.get("stars") or 0),
        "topics": list(item.get("tags") or [])[:8],
        "license": str(item.get("license") or ""),
        "url": url,
        "install_url": url,
    }


def load_registries(query: str = "", limit: int = 50) -> dict:
    items: list[dict] = []
    errors: list[str] = []
    for url in registry_urls():
        try:
            payload = github_json(url)
        except SkillInstallError as exc:
            errors.append(f"{url}：{exc}")
            continue
        name = str(payload.get("name") or url)
        for raw in payload.get("skills", [])[:limit]:
            if not isinstance(raw, dict):
                continue
            row = _normalize_registry(raw, name)
            if query.strip():
                haystack = f"{row['name']} {row['description']} {' '.join(row['topics'])}".lower()
                if query.strip().lower() not in haystack:
                    continue
            items.append({**row, "registry": name})
    return {"source": "registry", "query": query, "items": items, "errors": errors}


def _search_or_fallback(query: str, limit: int) -> dict:
    try:
        return search_github(query, limit)
    except SkillInstallError as exc:
        if "限流" not in str(exc):
            raise
        items = _curated_items(query)
        return {
            "source": "github",
            "query": query,
            "total": len(items),
            "items": items,
            "rate_limited": True,
            "note": "GitHub 搜索额度已用尽，暂时展示内置精选；配置 APP_GITHUB_TOKEN 可恢复实时搜索。",
        }


def browse(source: str = "github", query: str = "", limit: int = 20) -> dict:
    """市场浏览入口：source 支持 github / registry / all。"""
    source = source if source in ("github", "registry", "all") else "github"
    key = f"{source}|{query}|{limit}"
    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    if source == "registry":
        result = load_registries(query, limit)
    elif source == "all":
        gh = _search_or_fallback(query, limit)
        reg = load_registries(query, limit)
        result = {
            "source": "all",
            "query": query,
            "items": gh["items"] + reg["items"],
            "errors": reg["errors"],
            "total": gh.get("total", 0) + len(reg["items"]),
            "rate_limited": gh.get("rate_limited", False),
            "note": gh.get("note", ""),
        }
    else:
        result = _search_or_fallback(query, limit)

    if not result.get("rate_limited"):
        _cache_put(key, result)
    return {**result, "cached": False}


def known_sources() -> list[dict]:
    return [
        {
            "id": "github",
            "name": "GitHub 技能市场",
            "description": "按 topic 搜索公开 Agent Skills 仓库",
            "topics": topics(),
        },
        *[
            {"id": f"registry:{u}", "name": u, "description": "自建技能索引"}
            for u in registry_urls()
        ],
    ]
