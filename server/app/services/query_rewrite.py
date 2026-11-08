"""查询改写：多轮对话里的指代消解。

「它的第二个条件呢？」直接拿去检索什么都命不中——必须结合最近两轮
对话改写成独立查询。设计约束：

- 改写必须快：限 60 字输出、1.5s 超时、失败/超时一律回退原文查询，
  宁可检索差一点也不能让用户等
- LRU 缓存：同一追问在多轮里反复出现（重试、重新生成）不重复调模型
- 只在有多轮历史时调用；首轮任务本身就是独立查询
"""

import asyncio
import hashlib
from collections import OrderedDict

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm import complete

_REWRITE_SYSTEM = (
    "你是检索查询改写器。结合对话历史把用户的最新问题改写成一条"
    "不依赖上下文、可直接用于知识库检索的独立查询。"
    "只输出查询本身，不要回答问题、不要解释、不要加引号，"
    "最多 60 个字。若问题本身已是独立查询，原样输出。"
)

_REWRITE_TIMEOUT = 1.5   # 秒。改写是锦上添花，超时直接用原文
_CACHE_MAX = 128

_cache: OrderedDict[str, str] = OrderedDict()


def _cache_key(history: list[tuple[str, str]], query: str) -> str:
    digest = hashlib.sha256()
    for role, content in history[-2:]:
        digest.update(f"{role}:{content[:300]}\n".encode("utf-8"))
    digest.update(query.encode("utf-8"))
    return digest.hexdigest()


async def rewrite_query(history: list[tuple[str, str]], query: str) -> str:
    """把追问改写成独立检索查询；无历史、失败、超时都回退原文。"""
    query = (query or "").strip()
    if not history or not query:
        return query

    key = _cache_key(history, query)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached

    context = "\n".join(
        f"{'用户' if role == 'user' else '助手'}：{content[:300]}"
        for role, content in history[-2:]
    )
    try:
        result = await asyncio.wait_for(
            complete(
                [SystemMessage(content=_REWRITE_SYSTEM),
                 HumanMessage(content=f"对话历史：\n{context}\n\n最新问题：{query}")],
                temperature=0.0,
                max_tokens=120,
                kind="rewrite",
            ),
            timeout=_REWRITE_TIMEOUT,
        )
        rewritten, _ = result
    except Exception:  # noqa: BLE001 — 改写失败/超时不阻塞检索
        return query

    rewritten = rewritten.strip().strip('"「」').splitlines()[0].strip()
    if not rewritten or len(rewritten) > 120:
        return query

    _cache[key] = rewritten
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return rewritten
