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


@register("kb.ingest")
def _kb_ingest(job: Job) -> dict:
    """异步摄取一条上传的文档：解析 → 去重 → 切片 → 嵌入 → 入库。

    payload:
      path / kb_id / name / source — 由上传接口写入
    失败分类：格式不支持、文件缺失、内容为空属于永久失败（不重试）；
    嵌入端点不可用属于临时失败（队列按 max_attempts 重试）。
    """
    import asyncio
    from pathlib import Path

    from app.services.parsers import ParseError, parse_document
    from app.services.queue import get_queue
    from app.services.queue.base import PermanentJobError
    from app.services.rag import ingest_document
    from app.services.store import get_store

    queue = get_queue()
    data = job.payload or {}
    raw_path = data.get("path") or ""
    kb_id = data.get("kb_id") or ""
    name = data.get("name") or (Path(raw_path).name if raw_path else "未命名")

    def progress(stage: str, percent: int, **extra) -> None:
        queue.set_progress(job.id, {"stage": stage, "percent": percent, **extra})

    if not raw_path or not kb_id:
        raise PermanentJobError("任务缺少 path 或 kb_id")
    src = Path(raw_path)
    if not src.exists():
        raise PermanentJobError(f"上传文件已不存在：{raw_path}")

    progress("解析", 10)
    try:
        content = parse_document(src.read_bytes(), name)
    except ParseError as exc:
        raise PermanentJobError(str(exc)) from exc
    if not content.strip():
        raise PermanentJobError(f"{name}: 解析结果为空")

    progress("切片与嵌入", 40, chars=len(content))
    store = get_store()
    result = asyncio.run(
        ingest_document(
            store, kb_id, name, content, source=data.get("source") or "upload"
        )
    )

    progress("完成", 100)
    try:
        src.unlink()
    except OSError:
        pass
    return {
        "document_id": result.document.id,
        "name": result.document.name,
        "kb_id": kb_id,
        "version": result.version,
        "chunk_count": result.chunk_count,
        "deduped": result.deduped,
    }


@register("kb.reindex")
def _kb_reindex(job: Job) -> dict:
    """重建向量索引。

    payload:
      kb_id       — 只重建某个知识库；为空则全量重建
      document_id — 只重建某一篇（按当前切片策略重新切）
      drop        — 是否先丢弃集合（换嵌入模型、维度冲突时用）
    """
    import asyncio

    from app.services.rag import reindex_document
    from app.services.store import get_store
    from app.services.vector import get_vector_index

    payload = job.payload or {}
    kb_id = payload.get("kb_id") or None
    document_id = payload.get("document_id") or None

    if document_id:
        count = asyncio.run(reindex_document(get_store(), document_id))
        return {"document_id": document_id, "indexed": count}

    chunks = [c for c in get_store().list_chunks([kb_id] if kb_id else None) if c.vector]
    index = get_vector_index()
    if payload.get("drop"):
        written = index.rebuild(chunks)
    else:
        written = index.upsert(chunks)
    return {"indexed": written, "total_in_db": len(chunks), "index_count": index.count(kb_id)}
