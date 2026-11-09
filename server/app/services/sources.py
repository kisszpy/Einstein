"""文档源同步：网页入库后能重抓、能判断"这篇是不是过期了"。

知识库腐烂的头号原因不是没人用，是**里面的东西悄悄过期了**：制度改了三个月，
AI 还在按旧版答，而页面上没有任何一处提示"这份资料是两年前抓的"。
原先 URL 入库只把正文落下来，来源地址没存，抓取时间没存，于是既没法重抓，
也没法提示——文档成了不可追溯的孤儿。

这里补三件事：

1. **记来源**：`Document.meta` 里存 `url` / `fetched_at`（入库时写）；
2. **能重抓**：`refresh()` 重新抓一次，内容没变只更新时间戳（不重付嵌入成本），
   变了按"同名新版本"入库，旧内容进 `document_versions`，可回滚；
3. **能提示**：`staleness()` 按配置的阈值判过期，列表接口带 `stale` 标记。

为什么"没变就不重新嵌入"很重要：抓一次网页生成几百个切片，嵌入是花钱花时间
的那一步。内容没变还重嵌入，等于每天为同一份内容付一次钱。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db.models import Document
from app.services.parsers import ParseError, fetch_url
from app.services.rag import content_digest, ingest_document

DEFAULT_STALE_DAYS = 30


def now() -> str:
    """统一的时间戳格式（与 traces / 版本表一致，秒级 ISO8601 带时区）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_now = now


def stale_days() -> int:
    return int(getattr(get_settings(), "kb_stale_days", DEFAULT_STALE_DAYS) or DEFAULT_STALE_DAYS)


def source_url(doc: Document) -> str:
    meta = doc.meta or {}
    return str(meta.get("url") or "")


def fetched_at(doc: Document) -> str:
    meta = doc.meta or {}
    return str(meta.get("fetched_at") or "")


def is_stale(doc: Document, *, days: int | None = None) -> bool:
    """只对"有来源地址"的文档判过期。

    手工粘贴的文本没有"新鲜度"概念——它不会自己变旧，只会被人改。把这类文档
    也标成过期，列表里会一片红，用户很快学会无视这个标记。
    """
    if not source_url(doc):
        return False
    stamp = fetched_at(doc)
    if not stamp:
        return True
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    limit = days if days is not None else stale_days()
    return datetime.now(timezone.utc) - when > timedelta(days=limit)


def describe(doc: Document) -> dict:
    """列表项里关于来源的那几格。"""
    return {
        "source_url": source_url(doc),
        "fetched_at": fetched_at(doc),
        "stale": is_stale(doc),
    }


async def refresh(store, kb_id: str, doc: Document) -> dict:
    """重新抓取一篇 URL 文档。

    返回 {changed, version, chunk_count, fetched_at, error}。抓取失败不抛给
    调用方统一处理——签名保持"永远返回结果"，路由层再把 error 翻成 4xx/5xx，
    这样批量刷新时单篇失败不会中断整批。
    """
    url = source_url(doc)
    if not url:
        return {"changed": False, "error": "该文档不是从链接导入的，无法重抓"}

    try:
        title, content = fetch_url(url)
    except ParseError as exc:
        return {"changed": False, "error": str(exc)}

    stamp = _now()
    digest = content_digest(content)

    # 内容没变：只更新时间戳。重新嵌入是这条链路上最贵的一步，不白花。
    if digest == doc.content_hash:
        store.update_document(doc.id, meta={**(doc.meta or {}), "url": url, "fetched_at": stamp})
        store.save()
        return {
            "changed": False, "version": doc.version, "chunk_count": doc.chunk_count,
            "fetched_at": stamp, "error": "",
        }

    result = await ingest_document(
        store, kb_id, doc.name or title or url, content,
        source="url", dedupe=False, note="源同步更新",
        meta={"url": url, "fetched_at": stamp},
    )
    return {
        "changed": True,
        "version": result.version,
        "chunk_count": result.chunk_count,
        "fetched_at": stamp,
        "error": "",
    }


async def refresh_many(store, kb_id: str, docs: list[Document]) -> dict:
    """批量重抓：逐篇来，单篇失败只记错误不中断。"""
    results: list[dict] = []
    for doc in docs:
        outcome = await refresh(store, kb_id, doc)
        results.append({
            "id": doc.id, "name": doc.name,
            **outcome,
            "ok": not outcome.get("error"),
        })
    return {
        "total": len(results),
        "changed": sum(1 for r in results if r["changed"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "items": results,
    }
