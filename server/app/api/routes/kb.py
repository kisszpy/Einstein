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

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.core.config import get_settings
from app.services.parsers import ParseError, fetch_url, parse_document
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
