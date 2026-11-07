from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import current_workspace
from app.services.parsers import ParseError, parse_document
from app.services.rag import ingest_document, search
from app.services.store import KBStore, get_store

router = APIRouter(prefix="/kb", tags=["kb"])


class KBCreate(BaseModel):
    name: str


class DocCreate(BaseModel):
    name: str
    content: str


class SearchRequest(BaseModel):
    kb_ids: list[str]
    query: str
    top_k: int = 4
    mode: str | None = None  # vector / lexical / hybrid，空则用服务端默认


def _require_kb(store: KBStore, kb_id: str, ws: str) -> None:
    """跨工作区的访问一律按"不存在"处理，不泄露其他租户的资源存在性。"""
    if not store.owns(kb_id, ws):
        raise HTTPException(404, "知识库不存在")


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
        {"id": d.id, "name": d.name, "chunk_count": d.chunk_count}
        for d in store.documents_of_kb(kb_id)
    ]


@router.post("/{kb_id}/documents")
async def add_document(
    kb_id: str, req: DocCreate, ws: str = Depends(current_workspace)
) -> dict:
    """粘贴文本入库（开发期主通道）。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    try:
        doc = await ingest_document(store, kb_id, req.name, req.content)
    except RuntimeError as exc:  # 嵌入端点不可用
        raise HTTPException(503, str(exc)) from exc
    return {"id": doc.id, "name": doc.name, "chunk_count": doc.chunk_count}


@router.post("/{kb_id}/documents/upload")
async def upload_document(
    kb_id: str, file: UploadFile, ws: str = Depends(current_workspace)
) -> dict:
    """上传文档（PDF / docx / txt / md），解析后自动切片入库。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    raw = await file.read()
    try:
        content = parse_document(raw, file.filename or "未命名")
    except ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        doc = await ingest_document(store, kb_id, file.filename or "未命名", content)
    except RuntimeError as exc:  # 嵌入端点不可用
        raise HTTPException(503, str(exc)) from exc
    return {"id": doc.id, "name": doc.name, "chunk_count": doc.chunk_count}


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
    """删除知识库，连同其文档与切片一并清理。"""
    store = get_store()
    _require_kb(store, kb_id, ws)
    removed = store.delete_kb(kb_id)
    return {"ok": True, "removed_documents": removed}
