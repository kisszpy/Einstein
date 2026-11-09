"""嵌入客户端：优先走 OpenAI 兼容端点（llama.cpp / 云端），不可用时降级 mock。

mock 用 hashing trick（词袋哈希到定长向量），相似文本有一定相似度，
足以在真实嵌入模型未启动时验证检索管道的机制正确性。
"""

import hashlib
import math
import time
from collections.abc import Sequence

import httpx

from app.core.config import get_settings
from app.services import metrics

MOCK_DIM = 256


def _mock_embed_one(text: str) -> list[float]:
    """Hashing trick：英文按词、中文按字符二元组，落桶到定长向量。"""
    vec = [0.0] * MOCK_DIM

    def put(token: str) -> None:
        h = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big")
        idx = h % MOCK_DIM
        sign = 1.0 if (h >> 31) & 1 else -1.0
        vec[idx] += sign

    for word in text.lower().split():
        put(word)
    # 中文无空格分词，字符 bigram 保证查询与文档能撞上相同 token
    compact = "".join(text.split())
    for i in range(len(compact) - 1):
        put(compact[i : i + 2])

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def _remote_embed(texts: list[str]) -> list[list[float]] | None:
    """尝试调用 OpenAI 兼容 /v1/embeddings；失败返回 None。

    **分批**：一次请求塞整篇文档（300 页 PDF ≈ 1500 片、几十万字）必然超过
    60s 超时，用户看到的是"嵌入端点不可用"。按条数 + 字符数双阈值切批，
    逐批重试，顺序拼接——顺序与输入一致是调用方的硬要求。
    """
    settings = get_settings()
    out: list[list[float]] = []
    batches = _split_batches(
        list(texts), int(settings.embedding_batch_size), int(settings.embedding_batch_chars)
    )
    for batch in batches:
        vectors = await _remote_embed_batch(batch)
        if vectors is None:
            return None
        out.extend(vectors)
    return out


def _split_batches(texts: list[str], max_items: int, max_chars: int) -> list[list[str]]:
    """按条数 + 字符数切批，保持原顺序。

    双阈值缺一不可：500 条短句（每句 20 字）按条数切就够，但 20 条长文
    （每条 2 万字）按条数切仍然是 40 万字一个请求。
    """
    max_items = max(1, max_items)
    max_chars = max(1000, max_chars)
    batches: list[list[str]] = []
    current: list[str] = []
    chars = 0
    for text in texts:
        size = len(text)
        if current and (len(current) >= max_items or chars + size > max_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(text)
        chars += size
    if current:
        batches.append(current)
    return batches


async def _remote_embed_batch(texts: list[str]) -> list[list[float]] | None:
    """单批嵌入（原实现）：超时 60s、重试一次，失败返回 None。"""
    settings = get_settings()
    for attempt in range(2):
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                resp = await client.post(
                    f"{settings.embedding_base_url}/embeddings",
                    json={"model": settings.embedding_model, "input": texts},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                usage = resp.json().get("usage") or {}
                metrics.record_llm(
                    kind="embedding",
                    model=settings.embedding_model,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    prompt_tokens=usage.get("prompt_tokens"),
                )
                return [item["embedding"] for item in data]
        except Exception:  # noqa: BLE001 — 由 embed_texts 决定是否降级
            if attempt == 1:
                metrics.record_llm(
                    kind="embedding",
                    model=settings.embedding_model,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    status="error",
                )
                return None


async def embed_texts(
    texts: Sequence[str], *, allow_fallback: bool = False
) -> tuple[list[list[float]], str]:
    """返回 (向量列表, 使用哪套嵌入)。

    默认不允许降级：入库/检索混入 mock 向量（256 维）会与真实向量
    （4096 维）维度冲突，直接破坏整个库。降级仅供单测显式开启。
    """
    if texts:
        remote = await _remote_embed(list(texts))
        if remote is not None:
            return remote, "remote"
    if not allow_fallback:
        raise RuntimeError(
            f"嵌入端点不可用（{get_settings().embedding_base_url}），"
            "已禁止降级以保护向量库一致性"
        )
    return [_mock_embed_one(t) for t in texts], "mock"
