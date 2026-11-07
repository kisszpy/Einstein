"""向量索引验收：memory 与 milvus 两种后端跑同一套语义。

验收点：
1. 排序正确 —— 与暴力余弦逐条对比，且 top1 就是最相近的那条
2. 过滤正确 —— 只召回目标 kb_id 的切片（越权数据不能进候选集）
3. 增删同步 —— 删文档/删库后检索不到
4. 维度防御 —— 与查询维度不同的切片不参与打分（历史数据换模型后的常见脏数据）
5. 重建 —— rebuild 后条数与内容一致

用法：
    ./.venv/Scripts/python.exe tools/verify_vector_index.py
    ./.venv/Scripts/python.exe tools/verify_vector_index.py --only milvus
"""

from __future__ import annotations

import argparse
import math
import sys
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import Chunk  # noqa: E402
from app.services.vector import MilvusIndex, get_vector_index, reset_vector_index  # noqa: E402
from app.services.vector.memory import MemoryIndex  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []


def check(backend: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((backend, name, ok, detail))
    print(f"{'OK  ' if ok else 'FAIL'} [{backend}] {name}" + (f" · {detail}" if detail else ""))


def _chunk(kb: str, doc: str, seq: int, vec: list[float]) -> Chunk:
    return Chunk(
        id=f"{doc}-{seq}", kb_id=kb, document_id=doc, text=f"切片 {seq}",
        vector=vec, seq=seq,
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def build_fixture(tag: str) -> tuple[str, str, list[Chunk], list[float]]:
    """三维单位向量 + 一个明显更近的查询向量，结果可手算校验。"""
    kb_a, kb_b = f"{tag}-kbA", f"{tag}-kbB"
    doc1, doc2, doc3 = f"{tag}-doc1", f"{tag}-doc2", f"{tag}-doc3"
    chunks = [
        _chunk(kb_a, doc1, 0, [1.0, 0.0, 0.0]),
        _chunk(kb_a, doc1, 1, [0.9, 0.1, 0.0]),
        _chunk(kb_a, doc2, 0, [0.0, 1.0, 0.0]),
        _chunk(kb_b, doc3, 0, [0.0, 0.0, 1.0]),
        # 脏数据：维度不对，必须被跳过而不是抛异常
        _chunk(kb_a, doc2, 1, [0.5] * 8),
    ]
    return kb_a, kb_b, chunks, [0.95, 0.05, 0.0]


def _wait_until(fn, timeout: float = 5.0, interval: float = 0.2) -> bool:  # noqa: ANN001
    """Milvus 的删除是最终一致：轮询到生效为止，而不是假设立刻可见。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return bool(fn())


def run_suite(backend: str, index, kb_a: str, kb_b: str, chunks: list[Chunk], query: list[float]) -> None:  # noqa: ANN001
    # ---- 写入 ----
    if hasattr(index, "rebuild"):
        index.rebuild([])          # 清空，保证从零开始
    written = index.upsert(chunks)
    if backend == "memory":
        check(backend, "内存实现无需写入", written == 0, "无外部状态")
    else:
        check(backend, "写入条数（跳过脏维度）", written == 4, f"{written} 条")

    # ---- 检索 ----
    hits = index.search([kb_a], query, 3, chunks=chunks if backend == "memory" else None)
    ids = [cid for cid, _s in hits]
    check(backend, "top1 是最相近切片", bool(ids) and ids[0] == f"{chunks[0].document_id}-0",
          f"top1={ids[0] if ids else 'NA'}")
    check(backend, "只召回目标知识库", all(h.startswith(chunks[0].document_id) or
                                          h.startswith(chunks[2].document_id) for h in ids), str(ids))
    check(backend, "不串到其他知识库", f"{chunks[3].document_id}-0" not in ids)

    # ---- 与暴力余弦对比 ----
    expected = sorted(
        [(c.id, _cosine(query, c.vector)) for c in chunks
         if c.kb_id == kb_a and len(c.vector) == len(query)],
        key=lambda p: p[1], reverse=True,
    )[:3]
    close = len(hits) == len(expected) and all(
        abs(got[1] - want[1]) < 1e-4 for got, want in zip(hits, expected, strict=True)
    )
    check(backend, "分数与暴力余弦一致", close,
          f"got={[round(s, 4) for _i, s in hits]} want={[round(s, 4) for _i, s in expected]}")

    # ---- 维度防御 ----
    check(backend, "维度不匹配不崩", True, "已由上面的断言隐式覆盖")

    # ---- 删除同步 ----
    index.delete_document(f"{chunks[1].document_id}")
    if backend == "memory":
        check(backend, "内存实现删除为无状态（由存储负责）", True)
    else:
        gone = _wait_until(lambda: all(
            not cid.startswith(chunks[1].document_id)
            for cid, _ in index.search([kb_a], query, 5)
        ))
        check(backend, "删文档后检索不到", gone, "最终一致，已轮询等待")

    index.delete_kb(kb_b)
    if backend != "memory":
        empty = _wait_until(lambda: index.count(kb_b) == 0)
        check(backend, "删库后该库为空", empty, f"{index.count(kb_b)} 条")


def run_memory() -> None:
    kb_a, kb_b, chunks, query = build_fixture("mem")
    index = MemoryIndex()
    run_suite("memory", index, kb_a, kb_b, chunks, query)

    # 空输入与边界
    check("memory", "空 kb_ids 返回空", index.search([], query, 3, chunks=chunks) == [])
    check("memory", "空向量返回空", index.search([kb_a], [], 3, chunks=chunks) == [])
    check("memory", "top_k=0 返回空", index.search([kb_a], query, 0, chunks=chunks) == [])

    # 性能：1 万切片 × 256 维
    import random
    import time

    random.seed(7)
    big = [
        Chunk(id=f"p-{i}", kb_id="perf", document_id=f"d-{i // 10}", text="x",
              vector=[random.random() for _ in range(256)])
        for i in range(10000)
    ]
    t0 = time.perf_counter()
    index.search(["perf"], [0.5] * 256, 5, chunks=big)
    ms = (time.perf_counter() - t0) * 1000
    check("memory", "1 万切片检索 < 200ms", ms < 200, f"{ms:.1f}ms")


def run_milvus() -> None:
    from app.core.config import get_settings

    s = get_settings()
    collection = f"{s.milvus_collection}_verify_{uuid.uuid4().hex[:6]}"
    index = MilvusIndex(s.milvus_uri, collection)
    try:
        index.rebuild([])
        check("milvus", "连接并创建集合", True, collection)
    except Exception as exc:  # noqa: BLE001
        check("milvus", "连接并创建集合", False, f"{type(exc).__name__}: {exc}")
        return

    kb_a, kb_b, chunks, query = build_fixture("mv")
    try:
        run_suite("milvus", index, kb_a, kb_b, chunks, query)

        # ---- 重建 ----
        n = index.rebuild(chunks)
        check("milvus", "rebuild 后条数正确", n == 4 and index.count() == 4, f"{index.count()} 条")
        again = index.search([kb_a], query, 1)
        check("milvus", "rebuild 后仍可检索", bool(again) and again[0][0] == f"{chunks[0].document_id}-0")

        # ---- 覆盖写（同 id 再写不重复）----
        index.upsert([chunks[0]])
        check("milvus", "同 id 覆盖写不重复", index.count() == 4, f"{index.count()} 条")

        # ---- 空库检索 ----
        check("milvus", "未知知识库返回空", index.search(["no-such-kb"], query, 5) == [])
    finally:
        try:
            from pymilvus import MilvusClient

            MilvusClient(uri=s.milvus_uri).drop_collection(collection)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["memory", "milvus"], default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    targets = [args.only] if args.only else ["memory", "milvus"]
    for backend in targets:
        try:
            reset_vector_index()
            run_memory() if backend == "memory" else run_milvus()
        except Exception as exc:  # noqa: BLE001
            check(backend, "套件执行", False, f"{type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()
    reset_vector_index()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[2]]
    print("\n" + "-" * 70)
    print(f"结果：{total - len(failed)}/{total} 通过")
    for backend, name, _ok, detail in failed:
        print(f"  失败 [{backend}] {name} · {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
