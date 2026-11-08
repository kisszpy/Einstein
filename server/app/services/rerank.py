"""重排序（Rerank）提供者。

混合检索的 RRF 融合只看排名，头部排序经常"都对但不够准"；
用 reranker 给候选打绝对相关分，能把真正回答问题的片段顶到最前。

三级降级（配置 `rerank_provider`）：
- http：OpenAI 兼容 /rerank 端点（llama.cpp / vLLM / Jina 等都行），生产首选
- onnx：本机 bge-reranker（需要 onnxruntime + 模型文件，缺依赖自动不可用）
- none：不重排，检索结果原样返回（默认）

接口约束：批量打分、任何失败都返回 None 让调用方保留原排序——
重排是增益项，绝不因为它的故障拖垮检索主路径。
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from app.core.config import get_settings

RERANK_TIMEOUT = 3.0     # 秒。超时放弃重排，用 RRF 原序
RERANK_FACTOR = 3        # 取 top_k × 3 个候选进重排，再截回 top_k


class RerankProvider(Protocol):
    """批量打分：返回与 documents 等长的分数列表；失败返回 None。"""

    async def score(self, query: str, documents: list[str]) -> list[float] | None: ...

    @property
    def name(self) -> str: ...


class NoopRerank:
    """不重排。"""

    @property
    def name(self) -> str:
        return "none"

    async def score(self, query: str, documents: list[str]) -> list[float] | None:
        return None


class HttpRerank:
    """HTTP rerank 端点（OpenAI /v1/rerank 或兼容格式）。

    请求：{"model": ..., "query": ..., "documents": [...]}
    响应：{"results": [{"index": 0, "relevance_score": 0.97}, ...]}
    """

    def __init__(self, endpoint: str, model: str = "reranker", api_key: str = "") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "http"

    async def score(self, query: str, documents: list[str]) -> list[float] | None:
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            async with httpx.AsyncClient(timeout=RERANK_TIMEOUT) as client:
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    json={"model": self.model, "query": query, "documents": documents},
                )
            if resp.status_code != 200:
                return None
            results = resp.json().get("results") or []
            scores: list[float] | None = None
            for item in results:
                idx = int(item.get("index", -1))
                value = item.get("relevance_score", item.get("score"))
                if idx < 0 or idx >= len(documents) or value is None:
                    continue
                if scores is None:
                    scores = [0.0] * len(documents)
                scores[idx] = float(value)
            return scores
        except Exception:  # noqa: BLE001 — 任何网络/格式问题都放弃重排
            return None


class OnnxRerank:
    """本机 bge-reranker ONNX 推理。

    依赖 onnxruntime + transformers（分词）+ 模型文件（`rerank_model_dir`）。
    任何一项缺失时 `available=False`，上层自动走 none——不装也能跑。
    """

    def __init__(self, model_dir: str) -> None:
        self.model_dir = model_dir
        self._session = None
        self._tokenizer = None
        self._checked = False

    @property
    def name(self) -> str:
        return "onnx"

    def _ensure_loaded(self) -> bool:
        if self._checked:
            return self._session is not None
        self._checked = True
        try:
            import onnxruntime as ort  # noqa: F401
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
            self._session = ort.InferenceSession(
                f"{self.model_dir}/model.onnx", providers=["CPUExecutionProvider"]
            )
        except Exception:  # noqa: BLE001 — 依赖或模型缺失就永远走 none
            self._session = None
        return self._session is not None

    async def score(self, query: str, documents: list[str]) -> list[float] | None:
        if not self._ensure_loaded():
            return None
        import asyncio

        def _run() -> list[float]:
            pairs = [[query, d[:1500]] for d in documents]
            inputs = self._tokenizer(pairs, padding=True, truncation=True,
                                     max_length=512, return_tensors="np")
            feed = {k: v for k, v in inputs.items() if k in (
                i.name for i in self._session.get_inputs())}
            logits = self._session.run(None, feed)[0]
            # bge-reranker 输出单值 logit，过 sigmoid 得 0~1 分
            return [1.0 / (1.0 + pow(2.718281828, -float(x))) for x in logits]

        try:
            return await asyncio.to_thread(_run)
        except Exception:  # noqa: BLE001
            return None


def get_reranker() -> RerankProvider:
    """按配置构造 provider；构造失败一律退 none。"""
    settings = get_settings()
    kind = (settings.rerank_provider or "none").lower()
    if kind == "http" and settings.rerank_endpoint:
        return HttpRerank(settings.rerank_endpoint, settings.rerank_model,
                          settings.rerank_api_key)
    if kind == "onnx" and settings.rerank_model_dir:
        return OnnxRerank(settings.rerank_model_dir)
    return NoopRerank()


async def rerank_hits(
    query: str, hits: list, top_k: int,
) -> tuple[list, dict]:
    """对已融合的候选重排，截回 top_k。

    hits: list[RetrievedChunk]。返回 (重排后的 hits, 观测信息)。
    失败/关闭时原序返回——调用方无感知。
    """
    settings = get_settings()
    provider = get_reranker()
    info: dict = {"rerank": provider.name}
    if provider.name == "none" or len(hits) <= 1:
        return hits[:top_k], info

    width = min(len(hits), max(top_k * RERANK_FACTOR, top_k))
    candidates = hits[:width]
    t0 = time.perf_counter()
    scores = await provider.score(query, [h.text for h in candidates])
    info["rerank_ms"] = round((time.perf_counter() - t0) * 1000)

    if not scores or len(scores) != len(candidates):
        info["rerank_applied"] = False
        return hits[:top_k], info

    ranked = sorted(
        zip(candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True,
    )
    info["rerank_applied"] = True
    return [h for h, _ in ranked[:top_k]], info


def config_snapshot() -> dict:
    settings = get_settings()
    return {
        "provider": settings.rerank_provider,
        "endpoint": settings.rerank_endpoint,
        "model": settings.rerank_model,
    }
