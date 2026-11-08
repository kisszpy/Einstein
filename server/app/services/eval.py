"""RAG 评测集：改提示词/换模型前先跑回归。

评测集 = 一组 {query, expected_doc, expected_contains}：
- expected_doc：期望命中的文档名子串（检索质量判定）
- expected_contains：回答必须包含的关键词列表（回答质量判定，可空）

跑一次评测 = 逐条检索（记命中排名）→ 组上下文让模型作答 →
关键词校验，汇总 Recall@k / MRR / 回答通过率。
开发期 JSON 持久化（data/eval_sets.json），生产换 PostgreSQL，接口不变。
"""

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from app.services.state import blob

from langchain_core.messages import HumanMessage, SystemMessage

from app.services import metrics
from app.services.llm import complete
from app.services.rag import build_context_block, search
from app.services.store import get_store

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_STORE_PATH = _DATA_DIR / "eval_sets.json"
_RUNS_PATH = _DATA_DIR / "eval_runs.json"
_STATE = blob("eval", _STORE_PATH)
_RUN_STATE = blob("eval_runs", _RUNS_PATH)

EVAL_TOP_K = 3
EVAL_SYSTEM = (
    "你是问答助手。仅依据提供的参考资料回答，简洁直接；"
    "资料不足以回答时明确说明。不要输出思考过程。"
)
FAITHFULNESS_SYSTEM = (
    "你是忠实性裁判。给你一段「参考资料」和一个「回答」，"
    "判断回答是否完全基于参考资料、没有编造资料之外的事实。"
    "只输出 JSON：{\"faithful\": true/false, \"reason\": \"一句话理由\"}。"
)


@dataclass
class EvalItem:
    query: str
    expected_doc: str = ""
    expected_contains: list[str] = field(default_factory=list)
    expected_text: str = ""  # 命中切片须包含的原文片段（比文档名更细，单文档库也能区分）


@dataclass
class EvalSet:
    id: str
    kb_id: str
    name: str
    items: list[EvalItem] = field(default_factory=list)
    created_at: str = ""


# ---------- 持久化 ----------

_sets: dict[str, EvalSet] | None = None
_lock = threading.Lock()


def _load() -> dict[str, EvalSet]:
    try:
        payload = _STATE.read() or {}
        return {
            sid: EvalSet(
                id=v["id"],
                kb_id=v["kb_id"],
                name=v["name"],
                items=[EvalItem(**i) for i in v.get("items", [])],
                created_at=v.get("created_at", ""),
            )
            for sid, v in payload.items()
        }
    except Exception:  # noqa: BLE001 — 损坏则重建
        return {}


def _save() -> None:
    assert _sets is not None
    payload = {sid: asdict(s) for sid, s in _sets.items()}
    _STATE.write(payload)


def _registry() -> dict[str, EvalSet]:
    global _sets
    with _lock:
        if _sets is None:
            _sets = _load()
        return _sets


# ---------- CRUD ----------

def list_sets(kb_id: str | None = None) -> list[EvalSet]:
    sets = list(_registry().values())
    if kb_id:
        sets = [s for s in sets if s.kb_id == kb_id]
    return sets


def get_set(set_id: str) -> EvalSet | None:
    return _registry().get(set_id)


def add_set(kb_id: str, name: str, items: list[EvalItem]) -> EvalSet:
    es = EvalSet(
        id=uuid.uuid4().hex[:12],
        kb_id=kb_id,
        name=name,
        items=items,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    registry = _registry()
    with _lock:
        registry[es.id] = es
        _save()
    return es


def update_set(set_id: str, name: str | None = None, items: list[EvalItem] | None = None) -> EvalSet | None:
    registry = _registry()
    es = registry.get(set_id)
    if es is None:
        return None
    updates: dict = {}
    if name is not None:
        updates["name"] = name
    if items is not None:
        updates["items"] = items
    updated = replace(es, **updates)
    with _lock:
        registry[set_id] = updated
        _save()
    return updated


def remove_set(set_id: str) -> bool:
    registry = _registry()
    if set_id not in registry:
        return False
    with _lock:
        del registry[set_id]
        _save()
    return True


# ---------- 运行评测 ----------

def _judge_rank(expected_doc: str, expected_text: str, hits: list) -> int | None:
    """返回首个命中期望的切片排名（1 起），未命中 None。

    优先用 expected_text（切片原文片段）判定——同一份文档内部也能区分；
    未配置时退回 expected_doc（文档名子串）。
    """
    needle_text = expected_text.lower().strip()
    needle_doc = expected_doc.lower().strip()
    if not needle_text and not needle_doc:
        return None
    for i, h in enumerate(hits):
        if needle_text and needle_text in h.text.lower():
            return i + 1
        if not needle_text and needle_doc and needle_doc in h.document_name.lower():
            return i + 1
    return None


async def run_eval(
    set_id: str, mode: str | None = None, *, judge: bool = False, save: bool = True,
) -> dict | None:
    """跑一轮评测。

    judge：开启 LLM 裁判算忠实性（faithfulness）——逐条调模型，评测变慢，默认关。
    save：结果写入 eval_runs 历史（最近 20 轮），供两次运行对比。
    """
    es = _registry().get(set_id)
    if es is None:
        return None

    store = get_store()
    results: list[dict] = []
    t0 = time.perf_counter()

    for item in es.items:
        hits = await search(store, [es.kb_id], item.query, top_k=EVAL_TOP_K, mode=mode)  # type: ignore[arg-type]
        rank = _judge_rank(item.expected_doc, item.expected_text, hits)

        answer = ""
        answer_pass: bool | None = None
        faith: dict | None = None
        if hits:
            context = build_context_block(hits)
            prompt = f"=== 参考资料 ===\n{context}\n=== 参考资料 end ===\n\n问题：{item.query}"
            try:
                answer, _usage = await complete(
                    [SystemMessage(EVAL_SYSTEM), HumanMessage(prompt)],
                    temperature=0.2,
                )
                if item.expected_contains:
                    answer_pass = all(
                        k.lower() in answer.lower() for k in item.expected_contains
                    )
            except Exception as exc:  # noqa: BLE001 — 模型不可用不应作废整轮检索指标
                answer = f"（回答生成失败：{type(exc).__name__}）"
                answer_pass = None
            if judge and answer and not answer.startswith("（"):
                faith = await _judge_faithfulness(context, answer)
        else:
            answer = "（检索无命中，跳过回答）"

        results.append(
            {
                "query": item.query,
                "expected_doc": item.expected_doc,
                "expected_text": item.expected_text,
                "expected_contains": item.expected_contains,
                "rank": rank,
                "hit": rank is not None,
                "sources": [h.document_name for h in hits],
                "answer": answer,
                "answer_pass": answer_pass,
                "faithful": (faith or {}).get("faithful") if faith else None,
                "faith_reason": (faith or {}).get("reason") if faith else None,
            }
        )

    duration_ms = (time.perf_counter() - t0) * 1000
    n = len(results)
    ranks = [r["rank"] for r in results if r["rank"] is not None]
    answer_items = [r for r in results if r["answer_pass"] is not None]
    faith_items = [r for r in results if r["faithful"] is not None]

    # context precision：命中时排名越靠前越好（P@rank 的均值）；未命中记 0
    ctx_precision = (
        round(sum(1.0 / r for r in ranks) / n, 3) if n else 0
    )

    report = {
        "run_id": uuid.uuid4().hex[:12],
        "set_id": es.id,
        "name": es.name,
        "kb_id": es.kb_id,
        "mode": mode or "default",
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "duration_ms": round(duration_ms),
        "total": n,
        "recall_at_1": round(sum(1 for r in ranks if r == 1) / n, 3) if n else 0,
        "recall_at_k": round(len(ranks) / n, 3) if n else 0,
        "mrr": round(sum(1.0 / r for r in ranks) / n, 3) if n else 0,
        "context_precision": ctx_precision,
        "answer_pass_rate": round(
            sum(1 for r in answer_items if r["answer_pass"]) / len(answer_items), 3
        )
        if answer_items
        else None,
        "faithfulness": round(
            sum(1 for r in faith_items if r["faithful"]) / len(faith_items), 3
        )
        if faith_items
        else None,
        "items": results,
    }
    metrics.record_llm(
        kind="eval",
        model="batch",
        duration_ms=duration_ms,
        status="ok",
    )
    if save:
        _save_run(report)
    return report


async def _judge_faithfulness(context: str, answer: str) -> dict | None:
    """LLM 裁判：回答是否完全基于资料。失败返回 None，不影响其他指标。"""
    try:
        raw, _ = await complete(
            [SystemMessage(FAITHFULNESS_SYSTEM),
             HumanMessage(content=f"=== 参考资料 ===\n{context}\n=== 参考资料 end ===\n\n回答：{answer}")],
            temperature=0.0, max_tokens=150, kind="eval",
        )
        import json as _json

        start = raw.find("{")
        data = _json.loads(raw[start:raw.rfind("}") + 1])
        return {"faithful": bool(data.get("faithful")), "reason": str(data.get("reason", ""))[:200]}
    except Exception:  # noqa: BLE001 — 裁判不可用就跳过
        return None


# ---------- 运行历史（两次运行对比用） ----------

_RUNS_PER_SET = 20


def _runs_registry() -> dict[str, list[dict]]:
    try:
        return _RUN_STATE.read() or {}
    except Exception:  # noqa: BLE001
        return {}


def _save_run(report: dict) -> None:
    runs = _runs_registry()
    history = runs.setdefault(report["set_id"], [])
    history.append(report)
    runs[report["set_id"]] = history[-_RUNS_PER_SET:]
    _RUN_STATE.write(runs)


def list_runs(set_id: str, limit: int = _RUNS_PER_SET) -> list[dict]:
    """该评测集的历史运行（新→旧），不含逐条 items 以省流量。"""
    out = []
    for r in _runs_registry().get(set_id, [])[-limit:][::-1]:
        out.append({k: v for k, v in r.items() if k != "items"})
    return out


def get_run(set_id: str, run_id: str) -> dict | None:
    for r in _runs_registry().get(set_id, []):
        if r.get("run_id") == run_id:
            return r
    return None


def set_payload(es: EvalSet) -> dict:
    return asdict(es)
