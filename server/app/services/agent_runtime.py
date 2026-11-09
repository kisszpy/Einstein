"""智能体运行时公共件：事件拼装、消息构造、模型耗时埋点。

三个引擎（react / deepagents / deepagents-pro）对上层必须吐出同一套事件，
"历史消息 + 本轮任务 + 知识库预取""模型指标""收尾帧"这几件事的语义也完全
一致——统一放这里。引擎文件因此只剩两件事：怎么装配 agent、怎么把
LangGraph 的流翻成事件。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.services import metrics
from app.services.tools import preflight_kb

TOOL_PREVIEW = 300   # 步骤条里工具参数/结果的显示上限


def short(obj: Any, limit: int = TOOL_PREVIEW) -> str:
    """把一个对象压成单行摘要（工具参数、工具结果都用它）。"""
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


async def build_messages(
    task: str,
    history: list[dict] | None,
    kb_ids: list[str],
) -> list[tuple[str, str]]:
    """历史 + 本轮任务；首轮挂了知识库就先预取检索结果塞进上下文。

    预取只在首轮做：追问轮（有 history）里「再改一下」这类任务拿去检索
    只会带回噪音，那时由提示词规则驱动模型按需调 kb_search。
    """
    messages: list[tuple[str, str]] = [
        ("assistant" if h.get("role") == "assistant" else "user", h.get("content", ""))
        for h in (history or [])
    ]
    messages.append(("user", task))
    if kb_ids and not history:
        note = await preflight_kb(kb_ids, task)
        if note:
            messages.insert(0, ("user", note))
    return messages


class LlmTimer:
    """一次引擎运行的指标埋点：耗时、token、状态，退出时写 metrics。

    用法：
        with LlmTimer("agent", settings.llm_model) as timer:
            ...
            timer.status = "paused"      # 需要时改状态
            timer.tokens(prompt=1, completion=2)
    """

    def __init__(self, kind: str, model: str) -> None:
        self.kind = kind
        self.model = model
        self.status = "ok"
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self._t0 = time.perf_counter()

    def tokens(self, prompt: int | None, completion: int | None) -> None:
        """累加一次模型调用的 token 用量（流里逐条消息累加）。"""
        if prompt:
            self.prompt_tokens = (self.prompt_tokens or 0) + prompt
        if completion:
            self.completion_tokens = (self.completion_tokens or 0) + completion

    def __enter__(self) -> LlmTimer:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001 — 上下文协议签名
        # 消费者提前离场（GeneratorExit）不是失败，别把正常取消记成 error
        if exc_type is not None and not issubclass(exc_type, GeneratorExit) and self.status == "ok":
            self.status = "error"
        metrics.record_llm(
            kind=self.kind,
            model=self.model,
            duration_ms=(time.perf_counter() - self._t0) * 1000,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            status=self.status,
        )


def final_frame(ctx: Any, text: str, *, with_artifacts: bool = True) -> dict:
    """收尾帧：三个引擎的最后一个事件必须是它。"""
    return {
        "kind": "final",
        "text": text,
        "artifacts": list(ctx.artifacts) if (ctx is not None and with_artifacts) else [],
        "project": getattr(ctx, "project", "") if ctx is not None else "",
    }
