"""任务处理器注册表。

worker 只认 kind，不认业务：新增一种任务 = 加一个 handler 函数。
handler 约定：入参 Job，返回 dict（写入 job.result）；抛异常即失败，
由队列按 max_attempts 决定重试还是标记 failed。
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.queue.base import Job

HANDLERS: dict[str, Callable[[Job], dict]] = {}


def register(kind: str):
    def wrapper(fn: Callable[[Job], dict]) -> Callable[[Job], dict]:
        HANDLERS[kind] = fn
        return fn

    return wrapper


@register("noop")
def _noop(job: Job) -> dict:
    """自检用：原样回显 payload，验证队列链路。"""
    return {"echo": job.payload}


@register("kb.reindex")
def _kb_reindex(job: Job) -> dict:
    """重建向量索引。

    payload:
      kb_id  — 只重建某个知识库；为空则全量重建
      drop   — 是否先丢弃集合（换嵌入模型、维度冲突时用）
    """
    from app.services.store import get_store
    from app.services.vector import get_vector_index

    payload = job.payload or {}
    kb_id = payload.get("kb_id") or None
    chunks = [c for c in get_store().list_chunks([kb_id] if kb_id else None) if c.vector]
    index = get_vector_index()
    if payload.get("drop"):
        written = index.rebuild(chunks)
    else:
        written = index.upsert(chunks)
    return {"indexed": written, "total_in_db": len(chunks), "index_count": index.count(kb_id)}
