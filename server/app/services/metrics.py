"""可观测性：API 请求与模型调用指标。

开发期 JSON 持久化（data/metrics.json，各保留最近 N 条），
生产替换点：PostgreSQL 表 / OpenTelemetry 导出器。接口不变。
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_STORE_PATH = _DATA_DIR / "metrics.json"

MAX_REQUESTS = 2000
MAX_LLM = 1000

_lock = threading.Lock()
_cache: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load() -> dict:
    if not _STORE_PATH.exists():
        return {"requests": [], "llm": []}
    try:
        payload = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        return {"requests": payload.get("requests", []), "llm": payload.get("llm", [])}
    except Exception:  # noqa: BLE001 — 损坏则重建
        return {"requests": [], "llm": []}


def _save() -> None:
    assert _cache is not None
    _DATA_DIR.mkdir(exist_ok=True)
    _STORE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


def _store() -> dict:
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


# ---------- 记录 ----------

def record_request(method: str, path: str, status: int, duration_ms: float) -> None:
    with _lock:
        store = _store()
        store["requests"].append(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": _now(),
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": round(duration_ms, 1),
            }
        )
        del store["requests"][:-MAX_REQUESTS]
        _save()


def record_llm(
    kind: str,
    model: str,
    duration_ms: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    status: str = "ok",
) -> None:
    """记录一次模型调用。kind: chat / agent / embedding / eval。"""
    with _lock:
        store = _store()
        store["llm"].append(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": _now(),
                "kind": kind,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "duration_ms": round(duration_ms, 1),
                "status": status,
            }
        )
        del store["llm"][:-MAX_LLM]
        _save()


# ---------- 查询 ----------

def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def summary() -> dict:
    store = _store()
    reqs = store["requests"]
    llm = store["llm"]

    top_paths: dict[str, dict] = {}
    for r in reqs:
        agg = top_paths.setdefault(r["path"], {"count": 0, "total_ms": 0.0, "errors": 0})
        agg["count"] += 1
        agg["total_ms"] += r["duration_ms"]
        if r["status"] >= 400:
            agg["errors"] += 1
    top = sorted(top_paths.items(), key=lambda kv: -kv[1]["count"])[:8]

    llm_by_kind: dict[str, dict] = {}
    total_prompt = total_completion = 0
    known_tokens = False
    for c in llm:
        agg = llm_by_kind.setdefault(
            c["kind"], {"count": 0, "total_ms": 0.0, "errors": 0}
        )
        agg["count"] += 1
        agg["total_ms"] += c["duration_ms"]
        if c["status"] != "ok":
            agg["errors"] += 1
        if c["prompt_tokens"] is not None:
            known_tokens = True
            total_prompt += c["prompt_tokens"]
        if c["completion_tokens"] is not None:
            total_completion += c["completion_tokens"]

    return {
        "requests": {
            "total": len(reqs),
            "errors": sum(1 for r in reqs if r["status"] >= 400),
            "avg_ms": round(sum(r["duration_ms"] for r in reqs) / len(reqs), 1) if reqs else 0,
            "p95_ms": round(_p95([r["duration_ms"] for r in reqs]), 1),
            "top_paths": [
                {
                    "path": p,
                    "count": a["count"],
                    "avg_ms": round(a["total_ms"] / a["count"], 1),
                    "errors": a["errors"],
                }
                for p, a in top
            ],
        },
        "llm": {
            "total": len(llm),
            "avg_ms": round(sum(c["duration_ms"] for c in llm) / len(llm), 1) if llm else 0,
            "prompt_tokens": total_prompt if known_tokens or total_prompt else None,
            "completion_tokens": total_completion if known_tokens or total_completion else None,
            "by_kind": {
                k: {
                    "count": a["count"],
                    "avg_ms": round(a["total_ms"] / a["count"], 1),
                    "errors": a["errors"],
                }
                for k, a in llm_by_kind.items()
            },
        },
    }


def recent_requests(limit: int = 50) -> list[dict]:
    return list(reversed(_store()["requests"][-limit:]))


def recent_llm(limit: int = 50) -> list[dict]:
    return list(reversed(_store()["llm"][-limit:]))


def clear() -> None:
    global _cache
    with _lock:
        _cache = {"requests": [], "llm": []}
        _save()
