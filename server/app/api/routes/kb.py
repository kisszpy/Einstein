"""知识库：建库、摄取（同步 / 异步）、检索测试台、文档与切片管理。

摄取有两条路：
- 同步（POST /documents、/documents/upload）：小文本或小文件，请求内直接返回结果
- 异步（POST /documents/async、/documents/url）：大文件或大页面，落任务队列，
  立即返回 job_id，进度用 GET /jobs/{id} 轮询（stage / percent）

去重与版本：内容 sha256 相同直接复用已有文档；同库同名但内容不同则生成新版本，
旧内容快照进 document_versions，可按版本回滚。
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.core.config import get_settings
from app.services import batch as batch_io
from app.services.parsers import MAX_BYTES, ParseError, fetch_url, parse_document
from app.services.queue import get_queue
from app.services.rag import ingest_document, reembed_chunk, reindex_document, search
from app.services.store import KBStore, get_store
from app.services.vector import VectorIndexError, get_vector_index

router = APIRouter(prefix="/kb", tags=["kb"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
UPLOAD_DIR = DATA_DIR / "uploads"


class KBCreate(BaseModel):
    name: str


class DocCreate(BaseModel):
    name: str
    content: str
    strategy: str = "recursive"     # recursive | fixed | semantic
    chunk_size: int = 500
    chunk_overlap: int = 50


class UrlImport(BaseModel):
    url: str
    name: str | None = None


class DirImport(BaseModel):
    """服务端目录导入：直接扫描本机目录，零上传（需配置 APP_LOCAL_IMPORT_ROOTS）。"""

    path: str
    recursive: bool = True
    strategy: str = "recursive"
    chunk_size: int = 500
    chunk_overlap: int = 50


class ChunkPatch(BaseModel):
    text: str


class RollbackRequest(BaseModel):
    version: int


class SearchRequest(BaseModel):
    kb_ids: list[str]
    query: str
    top_k: int = 4
    mode: str | None = None  # vector / lexical / hybrid，空则用服务端默认


def _require_kb(store: KBStore, kb_id: str, ws: str) -> None:
    """跨工作区的访问一律按"不存在"处理，不泄露其他租户的资源存在性。"""
    if not store.owns(kb_id, ws):
        raise HTTPException(404, "知识库不存在")


def _require_doc(store: KBStore, kb_id: str, doc_id: str):
    doc = store.get_document(doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "文档不存在")
    return doc


def _ingest_payload(result) -> dict:  # noqa: ANN001 — IngestResult
    return {
        "id": result.document.id,
        "name": result.document.name,
        "chunk_count": result.chunk_count,
        "version": result.version,
        "deduped": result.deduped,
    }


async def _save_upload(
    upload: UploadFile, target: Path, limit: int = 0, *, hard_cap: int = MAX_BYTES
) -> int:
    """分块落盘。limit=0 表示整批不限；hard_cap 是单文件硬上限（zip 包允许更大）。"""
    size = 0
    cap = min(limit, hard_cap) if limit else hard_cap
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > cap:
                fh.close()
                target.unlink(missing_ok=True)
                if limit and limit < hard_cap:
                    raise HTTPException(413, "本批累计大小超过上限，请分批导入")
                raise HTTPException(413, f"文件超过 {cap // 1024 // 1024}MB 上限")
            fh.write(chunk)
    return size


def _enqueue_shards(
    items: list, *, kb_id: str, ws: str, source: str, delete_after: bool,
    strategy: str, chunk_size: int, chunk_overlap: int, shard: int,
    max_attempts: int = 3,
) -> list[dict]:
    """按 shard 切片入队。

    一个任务只会被一个 worker 槽消费，整批塞一个任务时并发度恒为 1；
    切开后 worker 有多少槽就能并行跑多少片，这才是批量导入的实际吞吐来源。
    """
    queue = get_queue()
    step = max(1, shard)
    jobs: list[dict] = []
    for start in range(0, len(items), step):
        part = items[start:start + step]
        job = queue.enqueue(
            "kb.ingest_batch",
            {
                "kb_id": kb_id,
                "items": [{"path": str(i.path), "name": i.name} for i in part],
                "source": source,
                "delete_after": delete_after,
                "strategy": strategy,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            },
            workspace_id=ws,
            max_attempts=max_attempts,
        )
        jobs.append({
            "job_id": job.id, "files": len(part),
            "bytes": sum(i.size for i in part), "status": job.status,
        })
        # 入队即写初始进度：否则前端聚合的分母要等每个任务被领走才知道，
        # 百分比会先高后低来回跳
        queue.set_progress(job.id, {
            "stage": "排队中", "percent": 0, "done": 0, "total": len(part),
            "current": "", "ok": 0, "failed": 0, "failed_items": [],
        })
    return jobs


def _cleanup_dir(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


@router.get("")
async def list_knowledge_bases(ws: str = Depends(current_workspace)) -> list[dict]:
    store = get_store()
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "document_count": len(store.documents_of_kb(kb.id)),
        }
        for kb in store.list_kbs(ws)
    ]


@router.post("")
async def create_kb(req: KBCreate, ws: str = Depends(current_workspace)) -> dict:
    store = get_store()
    kb = store.add_kb(req.name.strip(), workspace_id=ws)
    store.save()
    return {"id": kb.id, "name": kb.name}


@router.get("/{kb_id}/documents")
async def list_documents(kb_id: str, ws: str = Depends(current_workspace)) -> list[dict]:
    store = get_store()
    _require_kb(store, kb_id, ws)
    return [
        {
            "id": d.id,
            "name": d.name,
            "chunk_count": d.chunk_count,
            "version": d.version,
            "source": d.source,
            "status": d.status,
            "content_hash": d.content_hash[:12],
            "updated_at": d.updated_at,
        }
        for d in store.documents_of_kb(kb_id)
    ]


@router.post("/{kb_id}/documents")
async def add_document(
    kb_id: str, req: DocCreate, ws: str = Depends(current_workspace)
) -> dict:
    """粘贴文本入库（同步）。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    try:
        result = await ingest_document(
            store, kb_id, req.name, req.content,
            strategy=req.strategy, chunk_size=req.chunk_size, chunk_overlap=req.chunk_overlap,
        )
    except RuntimeError as exc:  # 嵌入端点不可用
        raise HTTPException(503, str(exc)) from exc
    return _ingest_payload(result)


@router.post("/{kb_id}/documents/upload")
async def upload_document(
    kb_id: str, file: UploadFile,
    strategy: str = "recursive", chunk_size: int = 500, chunk_overlap: int = 50,
    ws: str = Depends(current_workspace),
) -> dict:
    """上传并同步解析入库（小文件用；大文件走 /documents/async）。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    raw = await file.read()
    name = file.filename or "未命名"
    try:
        content = parse_document(raw, name)
    except ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        result = await ingest_document(
            store, kb_id, name, content, source="upload",
            strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return _ingest_payload(result)


@router.post("/{kb_id}/documents/async")
async def upload_document_async(
    kb_id: str, file: UploadFile, ws: str = Depends(current_workspace)
) -> dict:
    """上传即返回：文件落盘 + 入队，解析/切片/嵌入全部交给 worker。

    50MB 的 PDF 也不会占住 HTTP worker；前端拿 job_id 轮询进度。
    """
    store = get_store()
    _require_kb(store, kb_id, ws)

    settings = get_settings()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower()
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    with target.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 50 * 1024 * 1024:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, "文件超过 50MB 上限")
            fh.write(chunk)

    job = get_queue().enqueue(
        "kb.ingest",
        {
            "path": str(target),
            "kb_id": kb_id,
            "name": file.filename or target.name,
            "source": "upload",
        },
        workspace_id=ws,
        max_attempts=3,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "name": file.filename,
        "bytes": size,
        "backend": settings.queue_backend,
        "poll": f"/api/jobs/{job.id}",
    }


@router.post("/{kb_id}/documents/batch")
async def upload_documents_batch(
    kb_id: str,
    files: list[UploadFile] = File(..., description="多文件；也可只传一个 zip 自动解包"),
    paths: list[str] = Form(default=[], description="与 files 一一对应的相对路径（浏览器选目录时带上）"),
    strategy: str = Form("recursive", description="recursive | fixed | semantic"),
    chunk_size: int = Form(500),
    chunk_overlap: int = Form(50),
    ws: str = Depends(current_workspace),
) -> dict:
    """批量导入：一次收一个目录（多文件），或一个 ZIP 包。

    前端选文件夹时会把 `webkitRelativePath` 一起提交，"产品/需求.md" 就成了文档名，
    同类文件不会因重名覆盖，检索结果也能看出出处。整批入一个任务，
    进度用 GET /jobs/{id} 看 done/total 与失败清单。
    """
    store = get_store()
    _require_kb(store, kb_id, ws)

    settings = get_settings()
    max_files = settings.import_batch_max_files      # 0 = 不限制
    max_bytes = settings.import_batch_max_mb * 1024 * 1024  # 0 = 不限制

    batch_id = uuid.uuid4().hex[:12]
    staging = UPLOAD_DIR / f"batch-{batch_id}"
    staging.mkdir(parents=True, exist_ok=True)
    items: list[batch_io.BatchItem] = []
    skipped: list[batch_io.SkippedItem] = []
    total_bytes = 0
    from_archive = False

    # 只有一个文件且是 zip：解包后按内部目录结构导入
    if len(files) == 1:
        only = files[0]
        if Path(only.filename or "").suffix.lower() in (".zip", ".7z"):
            if Path(only.filename or "").suffix.lower() == ".7z":
                skipped.append(batch_io.SkippedItem(only.filename or "包", "仅支持 zip，请重新压缩"))
            else:
                archive = staging / "archive.zip"
                # 一个包里可以装很多小文件，所以包本身允许到 4GB，解压后按单文件 50MB 卡
                size = await _save_upload(
                    only, archive, max_bytes, hard_cap=4 * 1024 * 1024 * 1024
                )
                try:
                    items, skipped = batch_io.extract_zip(
                        archive, staging, max_files=max_files, max_bytes=max_bytes,
                    )
                except Exception as exc:  # noqa: BLE001 — 坏包按 400 返回，不抛 500
                    raise HTTPException(400, f"ZIP 解包失败：{exc}") from exc
                finally:
                    archive.unlink(missing_ok=True)
                from_archive = bool(items)
                total_bytes = size if not items else sum(i.size for i in items)
            files = []

    for index, upload in enumerate(files):
        raw_name = upload.filename or f"未命名-{index}"
        rel = batch_io.clean_rel_path(paths[index]) if index < len(paths) and paths[index] else \
            batch_io.clean_rel_path(raw_name)
        reason = batch_io.is_ignored(rel)
        if reason:
            skipped.append(batch_io.SkippedItem(rel, reason))
            continue
        if max_files and len(items) >= max_files:
            skipped.append(batch_io.SkippedItem(rel, f"超出单批 {max_files} 个文件上限"))
            continue
        if max_bytes and total_bytes >= max_bytes:
            skipped.append(batch_io.SkippedItem(rel, "累计大小超过上限"))
            continue
        target = staging / f"{len(items):05d}{Path(rel).suffix.lower()}"
        size = await _save_upload(upload, target, max_bytes - total_bytes if max_bytes else 0)
        total_bytes += size
        items.append(batch_io.BatchItem(target, rel, size))

    if not items:
        _cleanup_dir(staging)
        detail = "；".join(f"{s.name}（{s.reason}）" for s in skipped[:5]) or "没有可取的文件"
        raise HTTPException(400, f"没有可导入的文件：{detail}")

    jobs = _enqueue_shards(
        items, kb_id=kb_id, ws=ws,
        source="zip" if from_archive else "upload",
        delete_after=True,
        strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        shard=settings.import_batch_shard, max_attempts=3,
    )
    return {
        "batch_id": batch_id,
        "job_id": jobs[0]["job_id"],           # 兼容：单分片时就是全部
        "job_ids": [j["job_id"] for j in jobs],
        "jobs": jobs,
        "shards": len(jobs),
        "status": jobs[0]["status"],
        "files": len(items),
        "bytes": total_bytes,
        "skipped": [s.as_dict() for s in skipped[:50]],
        "skipped_count": len(skipped),
        "backend": settings.queue_backend,
        "poll": f"/api/jobs/{jobs[0]['job_id']}",
    }


@router.post("/{kb_id}/documents/dir")
async def import_directory(
    kb_id: str, req: DirImport, ws: str = Depends(current_workspace)
) -> dict:
    """导入服务端目录（本地部署用）：不复制文件，直接按相对路径入库。

    安全上默认关闭，需在 .env 配 `APP_LOCAL_IMPORT_ROOTS`（逗号分隔的绝对路径）；
    未配置时返回 403 并说明怎么开。符号链接与依赖目录（node_modules/.git 等）会跳过。
    """
    store = get_store()
    _require_kb(store, kb_id, ws)
    settings = get_settings()
    try:
        root = batch_io.resolve_allowed_root(req.path, settings.import_roots)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    items, skipped = batch_io.scan_directory(
        root, recursive=req.recursive,
        max_files=settings.import_batch_max_files,
        max_bytes=settings.import_batch_max_mb * 1024 * 1024,
    )
    if not items:
        detail = "；".join(f"{s.name}（{s.reason}）" for s in skipped[:5]) or "目录为空"
        raise HTTPException(400, f"没有可导入的文件：{detail}")

    jobs = _enqueue_shards(
        items, kb_id=kb_id, ws=ws, source="dir",
        delete_after=False,   # 源目录的文件不能删
        strategy=req.strategy, chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
        shard=settings.import_batch_shard, max_attempts=2,
    )
    return {
        "job_id": jobs[0]["job_id"],
        "job_ids": [j["job_id"] for j in jobs],
        "jobs": jobs,
        "shards": len(jobs),
        "status": jobs[0]["status"],
        "root": str(root),
        "files": len(items),
        "bytes": sum(i.size for i in items),
        "skipped": [s.as_dict() for s in skipped[:50]],
        "skipped_count": len(skipped),
        "backend": settings.queue_backend,
        "poll": f"/api/jobs/{jobs[0]['job_id']}",
    }


@router.post("/{kb_id}/documents/url")
async def import_url(
    kb_id: str, req: UrlImport, ws: str = Depends(current_workspace)
) -> dict:
    """抓网页入库（同步，限制 8MB）。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    try:
        title, content = fetch_url(req.url)
    except ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        result = await ingest_document(
            store, kb_id, req.name or title or req.url, content, source="url"
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {**_ingest_payload(result), "url": req.url}


@router.get("/{kb_id}/documents/{doc_id}/chunks")
async def list_document_chunks(
    kb_id: str, doc_id: str, ws: str = Depends(current_workspace)
) -> list[dict]:
    """切片预览：前端切片编辑器与引用定位都靠它。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    _require_doc(store, kb_id, doc_id)
    return [
        {
            "id": c.id,
            "seq": c.seq,
            "text": c.text,
            "chars": len(c.text),
            "char_start": c.char_start,
            "char_end": c.char_end,
            "embedding_kind": c.embedding_kind,
            "has_vector": bool(c.vector),
        }
        for c in sorted(store.list_chunks([kb_id], document_id=doc_id), key=lambda x: x.seq)
    ]


@router.patch("/{kb_id}/chunks/{chunk_id}")
async def update_chunk(
    kb_id: str, chunk_id: str, req: ChunkPatch, ws: str = Depends(current_workspace)
) -> dict:
    """改切片文本并重算向量，改完立刻可被检索命中。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "切片文本不能为空")
    try:
        chunk = await reembed_chunk(store, chunk_id, text)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if chunk is None or chunk.kb_id != kb_id:
        raise HTTPException(404, "切片不存在")
    return {"id": chunk.id, "text": chunk.text, "chars": len(chunk.text)}


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: str, doc_id: str, ws: str = Depends(current_workspace)
) -> dict:
    """删文档并级联清理切片与向量。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    _require_doc(store, kb_id, doc_id)
    removed = store.delete_document(doc_id)
    try:
        get_vector_index().delete_document(doc_id)
    except VectorIndexError:
        pass
    return {"ok": True, "removed_chunks": removed}


@router.post("/{kb_id}/documents/{doc_id}/reindex")
async def reindex_one(
    kb_id: str, doc_id: str, ws: str = Depends(current_workspace)
) -> dict:
    """按当前切片策略重建这一篇（版本号不变）。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    _require_doc(store, kb_id, doc_id)
    try:
        count = await reindex_document(store, doc_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "chunk_count": count}


@router.get("/{kb_id}/documents/{doc_id}/versions")
async def list_versions(
    kb_id: str, doc_id: str, ws: str = Depends(current_workspace)
) -> list[dict]:
    store = get_store()
    _require_kb(store, kb_id, ws)
    _require_doc(store, kb_id, doc_id)
    return [
        {
            "version": v["version"],
            "chunk_count": v["chunk_count"],
            "chars": len(v.get("content") or ""),
            "source": v.get("source", ""),
            "note": v.get("note", ""),
            "created_at": v.get("created_at", ""),
        }
        for v in store.repo.list_versions(doc_id)
    ]


@router.post("/{kb_id}/documents/{doc_id}/rollback")
async def rollback_document(
    kb_id: str, doc_id: str, req: RollbackRequest, ws: str = Depends(current_workspace)
) -> dict:
    """回滚到指定版本：把那份内容作为新版本重新入库（历史不丢）。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    doc = _require_doc(store, kb_id, doc_id)
    snapshot = store.repo.get_version(doc_id, req.version)
    if snapshot is None:
        raise HTTPException(404, f"版本 v{req.version} 不存在")
    try:
        result = await ingest_document(
            store, kb_id, doc.name, snapshot["content"],
            source=snapshot.get("source") or "rollback",
            dedupe=False, note=f"回滚到 v{req.version}",
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {**_ingest_payload(result), "rolled_back_to": req.version}


@router.post("/search")
async def search_kb(req: SearchRequest, ws: str = Depends(current_workspace)) -> list[dict]:
    """检索测试台：直接看召回效果，可切换检索模式对比。"""
    store = get_store()
    kb_ids = store.owned_kb_ids(ws, req.kb_ids)
    if not kb_ids:
        return []
    try:
        hits = await search(store, kb_ids, req.query, req.top_k, mode=req.mode)  # type: ignore[arg-type]
    except RuntimeError as exc:  # 嵌入端点不可用：与入库一致，回 503 而不是 500
        raise HTTPException(503, str(exc)) from exc
    return [
        {
            "chunk_id": h.chunk_id,
            "document_name": h.document_name,
            "text": h.text,
            "score": h.score,
            "vector_score": h.vector_score,
            "lexical_score": h.lexical_score,
        }
        for h in hits
    ]


@router.delete("/{kb_id}")
async def delete_kb(kb_id: str, ws: str = Depends(current_workspace)) -> dict:
    """删除知识库，连同其文档、切片与向量一并清理。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    doc_ids = [d.id for d in store.documents_of_kb(kb_id)]
    removed = store.delete_kb(kb_id)
    index = get_vector_index()
    try:
        index.delete_kb(kb_id)
    except VectorIndexError:
        pass
    return {"ok": True, "removed_documents": removed, "removed_documents_indexed": len(doc_ids)}
