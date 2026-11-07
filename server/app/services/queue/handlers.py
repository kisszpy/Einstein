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

    store = get_store()
    if store.repo.get_kb(kb_id) is None:
        # 任务入队后库可能已被删除。此时继续入库会写出孤儿文档/切片/向量：
        # 列表里看不到（库没了），却占存储、污染向量索引，只能手工清。
        # 这种情况重试也不会好，判永久失败并清掉落盘文件。
        try:
            src.unlink()
        except OSError:
            pass
        raise PermanentJobError(f"知识库 {kb_id} 已被删除，放弃入库")

    progress("解析", 10)
    try:
        content = parse_document(src.read_bytes(), name)
    except ParseError as exc:
        raise PermanentJobError(str(exc)) from exc
    if not content.strip():
        raise PermanentJobError(f"{name}: 解析结果为空")

    progress("切片与嵌入", 40, chars=len(content))
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


@register("kb.ingest_batch")
def _kb_ingest_batch(job: Job) -> dict:
    """批量摄取：把一个目录/一批上传文件当成一个任务，逐篇消化。

    payload:
      kb_id        — 目标知识库
      items        — [{path, name}]，name 是相对路径（作为文档名）
      source       — upload | zip | dir
      delete_after — 是否在成功后删掉落盘暂存文件（目录导入为 False，源文件不能动）

    失败策略：单个文件解析失败只记进 failed 列表、继续下一篇（目录里混一个坏文件
    不该让整批白跑）；但如果一篇都没成功、且原因是运行时（嵌入端点不可用），
    就抛出异常让队列按 max_attempts 重试——此时没有任何源文件被删，重试是安全的。
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
    kb_id = data.get("kb_id") or ""
    items = data.get("items") or []
    source = data.get("source") or "upload"
    delete_after = bool(data.get("delete_after", source != "dir"))
    strategy = data.get("strategy") or "recursive"
    chunk_size = int(data.get("chunk_size") or 500)
    chunk_overlap = int(data.get("chunk_overlap") or 50)
    if not kb_id:
        raise PermanentJobError("任务缺少 kb_id")
    if not items:
        raise PermanentJobError("任务没有待处理文件")

    store = get_store()
    if store.repo.get_kb(kb_id) is None:
        for item in items:
            if delete_after:
                try:
                    Path(item.get("path") or "").unlink()
                except OSError:
                    pass
        raise PermanentJobError(f"知识库 {kb_id} 已被删除，放弃入库")

    total = len(items)
    ok = 0
    deduped = 0
    chunks = 0
    failed: list[dict] = []
    documents: list[dict] = []
    retryable = 0

    def report(done: int, current: str = "", stage: str = "") -> None:
        queue.set_progress(job.id, {
            "stage": stage or f"导入 {done}/{total}",
            "percent": min(99, int(done * 100 / total)) if total else 0,
            "done": done, "total": total, "current": current,
            "ok": ok, "failed": len(failed), "failed_items": failed[:20],
        })

    for index, item in enumerate(items, start=1):
        raw_path = (item or {}).get("path") or ""
        name = (item or {}).get("name") or (Path(raw_path).name if raw_path else "未命名")
        src = Path(raw_path)
        report(index - 1, name)

        if not raw_path or not src.exists():
            failed.append({"name": name, "error": "文件已不存在"})
            continue
        try:
            content = parse_document(src.read_bytes(), name)
        except ParseError as exc:
            failed.append({"name": name, "error": str(exc)})
            if delete_after:
                try:
                    src.unlink()
                except OSError:
                    pass
            continue
        if not content.strip():
            failed.append({"name": name, "error": "解析结果为空"})
            if delete_after:
                try:
                    src.unlink()
                except OSError:
                    pass
            continue

        try:
            result = asyncio.run(ingest_document(
                store, kb_id, name, content, source=source,
                strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            ))
        except RuntimeError as exc:
            # 嵌入/向量服务不可用：继续跑只会重复同一个错误，留给队列重试
            retryable += 1
            failed.append({"name": name, "error": str(exc)})
            break
        ok += 1
        deduped += 1 if result.deduped else 0
        chunks += result.chunk_count
        if len(documents) < 50:
            documents.append({
                "id": result.document.id, "name": result.document.name,
                "chunk_count": result.chunk_count, "deduped": result.deduped,
            })
        if delete_after:
            try:
                src.unlink()
            except OSError:
                pass

    if ok == 0 and retryable:
        raise RuntimeError(failed[-1]["error"] if failed else "批量导入失败")

    queue.set_progress(job.id, {
        "stage": "完成", "percent": 100, "done": total, "total": total,
        "current": "", "ok": ok, "failed": len(failed), "failed_items": failed[:20],
    })
    return {
        "kb_id": kb_id, "total": total, "ok": ok, "deduped": deduped,
        "failed": len(failed), "failed_items": failed[:20],
        "chunks": chunks, "documents": documents,
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

    from app.services.queue.base import PermanentJobError
    from app.services.rag import reindex_document
    from app.services.store import get_store
    from app.services.vector import get_vector_index

    payload = job.payload or {}
    kb_id = payload.get("kb_id") or None
    document_id = payload.get("document_id") or None

    if document_id:
        store = get_store()
        if store.get_document(document_id) is None:
            raise PermanentJobError(f"文档 {document_id} 已不存在（可能随知识库一起删除了）")
        count = asyncio.run(reindex_document(store, document_id))
        return {"document_id": document_id, "indexed": count}

    chunks = [c for c in get_store().list_chunks([kb_id] if kb_id else None) if c.vector]
    index = get_vector_index()
    if payload.get("drop"):
        written = index.rebuild(chunks)
    else:
        written = index.upsert(chunks)
    return {"indexed": written, "total_in_db": len(chunks), "index_count": index.count(kb_id)}
