"""检索门面：所有入口共用同一套检索参数。

**为什么要有这一层**：同一份 `search()`，对话链路用「改写 + 父片展开 + top_k=4
+ 回传引用」，智能体链路用「不改写 + 不展开 + top_k=3 + 不回引用」。同一份
资料，问一句话得到一个答案，让智能体做同一件事得到另一个答案，而且后者更差。
这类不一致最难排查——两个入口都"能用"。所以参数只在这里定义，两条链路都调它。

`for_answer=True` 表示结果要直接进模型上下文（要父片、要少而准）；
`for_tool=True` 表示作为工具结果给模型自己再筛（带分数、带来源编号）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.rag import RetrievedChunk, search
from app.services.store import get_store

DEFAULT_TOP_K = 4

__all__ = ["DEFAULT_TOP_K", "Retrieval", "retrieve"]


@dataclass
class Retrieval:
    hits: list[RetrievedChunk] = field(default_factory=list)
    query: str = ""
    citations: list[dict] = field(default_factory=list)
    # 是否做了查询改写（追问消解），前端/日志用它解释"为什么检索词变了"
    rewritten: bool = False

    @property
    def missed(self) -> bool:
        return not self.hits


def _citation(index: int, hit: RetrievedChunk, query: str) -> dict:
    return {
        "index": index,
        "document_name": hit.document_name,
        "chunk_id": hit.chunk_id,
        "text": hit.text,
        "score": hit.rerank_score if hit.rerank_score is not None else hit.score,
        "vector_score": hit.vector_score,
        "query": query,
    }


async def retrieve(
    kb_ids: list[str],
    query: str,
    *,
    history: list[tuple[str, str]] | None = None,
    top_k: int = DEFAULT_TOP_K,
    parent_expand: bool = True,
) -> Retrieval:
    """检索（含追问改写、相关性门限、父片展开、引用构造）。

    history：前几轮对话。非空时先做一次指代消解——「它的第二个条件呢」直接
    拿去检索必然命不中；改写失败不阻塞，用原句继续。
    """
    if not kb_ids or not query.strip():
        return Retrieval(query=query)

    search_query = query
    rewritten = False
    if history:
        try:
            from app.services.query_rewrite import rewrite_query

            search_query = await rewrite_query(history, query) or query
            rewritten = search_query.strip() != query.strip()
        except Exception as exc:  # noqa: BLE001 — 改写是增益项，失败用原句
            print(f"[retrieval] 查询改写失败：{type(exc).__name__}: {exc}", flush=True)

    hits = await search(
        get_store(), kb_ids, search_query,
        top_k=top_k, parent_expand=parent_expand,
    )
    return Retrieval(
        hits=hits,
        query=search_query,
        citations=[_citation(i + 1, h, search_query) for i, h in enumerate(hits)],
        rewritten=rewritten,
    )
