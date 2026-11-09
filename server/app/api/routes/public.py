"""公开访问（无鉴权）：拿着 token 就能读一份内容，仅此一份。

这里的每个入口都不挂登录依赖，所以格外小心三件事：
1. **泄密半径**：返回体只由 token 指向的那一份内容组装，绝不带知识库清单、
   文档列表、同空间其它会话这类"顺带"信息；
2. **花费**：允许追问的分享等于开了一个匿名 LLM 入口，按 IP 限流，
   且不允许流式（省得挂住连接白烧 token）；
3. **存在性**：过期、撤销、不存在一律 404，不区分——区分了就等于告诉
   扫描者"这个 token 曾经存在"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.services import ratelimit, shares
from app.services.history import get_history_store
from app.services.llm import complete
from app.services.rag import build_context_block
from app.services.retrieval import retrieve
from app.services.store import get_store

router = APIRouter(prefix="/public", tags=["public"])

ASK_SYSTEM = (
    "你是一个严谨的问答助手。只依据下面提供的参考资料回答，"
    "并在引用到某条资料时在句末标注 [编号]。资料不足以回答时，"
    "直接说明资料中没有相关内容，不要编造。回答用中文，先给结论再分点。"
    "\n\n=== 参考资料 ===\n{context}\n=== 参考资料 end ==="
)


def _ip(request: Request) -> str:
    # 反代后 client.host 是网关地址；X-Forwarded-For 第一段才是真实来源。
    # 这里只用于限流，被伪造的后果是多一个桶，不影响鉴权。
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _live_share(token: str) -> dict:
    share = shares.get_share(token)
    if not shares.is_live(share):
        raise HTTPException(404, "链接已失效或不存在")
    return share  # type: ignore[return-value]


@router.get("/shares/{token}")
async def read_share(token: str, request: Request) -> dict:
    """免登录读取：会话正文，或知识库的问答页元信息。"""
    if not ratelimit.allow(f"share-read:{_ip(request)}", 120, 60)[0]:
        raise HTTPException(429, "访问太频繁，请稍后再试")
    share = _live_share(token)
    shares.touch_view(token)
    body: dict = {
        "kind": share["kind"],
        "title": share.get("title", ""),
        "allow_ask": bool(share.get("allow_ask")),
        "expires_at": share.get("expires_at", ""),
        "created_at": share.get("created_at", ""),
    }
    if share["kind"] == "conversation":
        conv = get_history_store().get_conversation(share.get("target_id", ""))
        # 会话被删了，分享跟着失效——不留一条指向空内容的链接
        if conv is None:
            raise HTTPException(404, "链接已失效或不存在")
        body["conversation"] = {
            "title": conv.title,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "messages": [
                {"role": m.role, "content": m.content, "created_at": m.created_at}
                for m in conv.messages
            ],
        }
    else:
        kb = get_store().get_kb(share.get("target_id", ""))
        if kb is None:
            raise HTTPException(404, "链接已失效或不存在")
        body["kb"] = {"name": kb.name, "description": getattr(kb, "description", "") or ""}
    return body


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@router.post("/shares/{token}/ask")
async def ask_share(token: str, req: AskRequest, request: Request) -> dict:
    """匿名追问（仅知识库分享、且分享者显式打开 allow_ask）。"""
    if not ratelimit.allow(f"share-ask:{_ip(request)}", 6, 60)[0]:
        raise HTTPException(429, "提问太频繁，请稍后再试")
    share = _live_share(token)
    if share["kind"] != "kb":
        raise HTTPException(403, "这份分享不支持提问")
    if not share.get("allow_ask"):
        raise HTTPException(403, "分享者未开启提问权限")

    question = req.question.strip()
    result = await retrieve([share["target_id"]], question, top_k=4)
    if result.error:
        return {"answer": "", "citations": [], "missed": False, "error": result.error}
    if not result.hits:
        return {
            "answer": "资料中没有找到与这个问题相关的内容。",
            "citations": [], "missed": True,
        }

    messages = [
        SystemMessage(content=ASK_SYSTEM.format(context=build_context_block(result.hits))),
        HumanMessage(content=question),
    ]
    answer, _usage = await complete(
        messages, temperature=0.3, max_tokens=800, kind="chat",
        workspace_id=share.get("workspace_id", ""),
    )
    citations = [
        {"index": c.get("index"), "document_name": c.get("document_name", ""),
         "text": (c.get("text") or "")[:240]}
        for c in result.citations
    ]
    return {"answer": answer, "citations": citations, "missed": False}
