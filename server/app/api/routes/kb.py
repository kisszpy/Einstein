from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app.services.parsers import ParseError, parse_document
from app.services.rag import ingest_document, search
from app.services.store import get_store

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


@router.get("")
async def list_knowledge_bases() -> list[dict]:
    store = get_store()
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "document_count": len(store.documents_of_kb(kb.id)),
        }
        for kb in store.list_kbs()
    ]


@router.post("")
async def create_kb(req: KBCreate) -> dict:
    store = get_store()
    kb = store.add_kb(req.name.strip())
    store.save()
    return {"id": kb.id, "name": kb.name}


@router.get("/{kb_id}/documents")
async def list_documents(kb_id: str) -> list[dict]:
    store = get_store()
    if kb_id not in store.kbs:
        raise HTTPException(404, "知识库不存在")
    return [
        {"id": d.id, "name": d.name, "chunk_count": d.chunk_count}
        for d in store.documents_of_kb(kb_id)
    ]


@router.post("/{kb_id}/documents")
async def add_document(kb_id: str, req: DocCreate) -> dict:
    """粘贴文本入库（开发期主通道）。"""
    store = get_store()
    if kb_id not in store.kbs:
        raise HTTPException(404, "知识库不存在")
    try:
        doc = await ingest_document(store, kb_id, req.name, req.content)
    except RuntimeError as exc:  # 嵌入端点不可用
        raise HTTPException(503, str(exc)) from exc
    return {"id": doc.id, "name": doc.name, "chunk_count": doc.chunk_count}


@router.post("/{kb_id}/documents/upload")
async def upload_document(kb_id: str, file: UploadFile) -> dict:
    """上传文档（PDF / docx / txt / md），解析后自动切片入库。"""
    store = get_store()
    if kb_id not in store.kbs:
        raise HTTPException(404, "知识库不存在")
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
async def search_kb(req: SearchRequest) -> list[dict]:
    """检索测试台：直接看召回效果。"""
    hits = await search(get_store(), req.kb_ids, req.query, req.top_k)
    return [
        {
            "chunk_id": h.chunk_id,
            "document_name": h.document_name,
            "text": h.text,
            "score": h.score,
        }
        for h in hits
    ]
