"""容量与规模基线：回答"这套部署现在多大、还能撑多久"。

为什么不是又一个监控面板：监控看的是"现在有没有事"，容量看的是"三个月后
会不会有事"。两者的判据不同——前者看错误率与延迟分位，后者看"每个切片占
多少磁盘、每千次检索要多少算力"。所以做成一次性报告，可以存进变更记录，
两次报告一对比就知道增长斜率。

用法：
    python tools/report_capacity.py
    python tools/report_capacity.py --probe "死锁"     # 附带实测一次检索延迟
    python tools/report_capacity.py --json > cap.json  # 机器可读，便于画趋势
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SERVER_ROOT / "data"
BACKUP_DIR = SERVER_ROOT / "backups"


def _mb(num: float) -> float:
    return round(num / 1024 / 1024, 1)


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def collect() -> dict:
    from app.core.config import get_settings
    from app.services.store import get_store
    from app.services.vector import get_vector_index

    settings = get_settings()
    out: dict = {
        "backend": {
            "store": settings.store_backend,
            "vector": settings.vector_backend,
            "queue": settings.queue_backend,
        },
        "scale": {},
        "storage_mb": {},
        "errors": [],
    }

    store = get_store()
    kbs = store.list_kbs()
    docs = 0
    chunks = 0
    per_kb: list[dict] = []
    for kb in kbs:
        try:
            d = len(store.documents_of_kb(kb.id))
        except Exception as exc:  # noqa: BLE001
            d = 0
            out["errors"].append(f"docs({kb.id}): {exc}")
        try:
            c = store.count_chunks(kb.id)
        except Exception as exc:  # noqa: BLE001
            c = 0
            out["errors"].append(f"chunks({kb.id}): {exc}")
        docs += d
        chunks += c
        per_kb.append({"id": kb.id, "name": kb.name, "documents": d, "chunks": c})

    try:
        vectors = get_vector_index().count()
    except Exception as exc:  # noqa: BLE001
        vectors = 0
        out["errors"].append(f"vectors: {exc}")

    out["scale"] = {
        "knowledge_bases": len(kbs),
        "documents": docs,
        "chunks": chunks,
        "vectors": vectors,
        # 平均切片长度是切片策略是否合理的直接证据：太长检索不精准，
        # 太碎则上下文拼不回去、账单也会涨（每次检索都多带几段）
        "per_kb": sorted(per_kb, key=lambda k: -k["chunks"])[:10],
    }

    # 存储占用
    sqlite_path = Path(settings.sqlite_path)
    if not sqlite_path.is_absolute():
        sqlite_path = SERVER_ROOT / sqlite_path
    storage = {
        "data_dir": _mb(_dir_size(DATA_DIR)),
        "uploads": _mb(_dir_size(DATA_DIR / "uploads")),
        "backups": _mb(_dir_size(BACKUP_DIR)),
        "sqlite": _mb(sqlite_path.stat().st_size) if sqlite_path.exists() else 0.0,
    }
    if settings.store_backend == "postgres":
        try:
            from app.db.engine import get_db

            row = get_db().query_one("SELECT pg_database_size(current_database()) AS bytes")
            storage["postgres"] = _mb(float(row["bytes"])) if row else 0.0
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"pg_size: {exc}")
    out["storage_mb"] = storage
    return out


async def probe(query: str) -> dict:
    """实测一次检索（不含生成）：延迟主要来自嵌入请求与向量/BM25 两路召回。"""
    from app.services.retrieval import retrieve
    from app.services.store import get_store

    store = get_store()
    kb_ids = [kb.id for kb in store.list_kbs()][:5]
    if not kb_ids:
        return {"skipped": "没有知识库"}
    start = time.perf_counter()
    result = await retrieve(kb_ids, query, top_k=4)
    elapsed = int((time.perf_counter() - start) * 1000)
    return {
        "query": query,
        "kb_count": len(kb_ids),
        "hits": len(result.hits),
        "missed": result.missed,
        "error": result.error,
        "latency_ms": elapsed,
    }


def render(report: dict) -> None:
    backend = report["backend"]
    scale = report["scale"]
    storage = report["storage_mb"]
    print("== 后端 ==")
    print(f"  store={backend['store']}  vector={backend['vector']}  queue={backend['queue']}")
    print("\n== 规模 ==")
    print(f"  知识库 {scale['knowledge_bases']}  文档 {scale['documents']}  "
          f"切片 {scale['chunks']}  向量 {scale['vectors']}")
    if scale["per_kb"]:
        print("  最大的知识库：")
        for kb in scale["per_kb"]:
            print(f"    {kb['name'][:24]:<26} 文档 {kb['documents']:>4}  切片 {kb['chunks']:>6}")
    print("\n== 占用（MB）==")
    for key, value in storage.items():
        print(f"  {key:<10} {value:>8}")
    probe = report.get("probe")
    if probe:
        print("\n== 检索实测 ==")
        if probe.get("skipped"):
            print(f"  跳过：{probe['skipped']}")
        else:
            print(f"  {probe['latency_ms']} ms  · 命中 {probe['hits']} 条"
                  f"  · 覆盖 {probe['kb_count']} 个库"
                  f"{'  · 未命中' if probe['missed'] else ''}"
                  f"{'  · 错误：' + probe['error'] if probe.get('error') else ''}")
    if report["errors"]:
        print("\n== 采集问题 ==")
        for err in report["errors"]:
            print(f"  ! {err}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="", help="附带实测一次检索延迟")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    report = collect()
    if args.probe:
        report["probe"] = asyncio.run(probe(args.probe))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
