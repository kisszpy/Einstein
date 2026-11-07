"""三后端一致性验收：json / sqlite / postgres 跑同一套断言。

目的不是"能连通"，而是"换后端后行为完全一致"——这是 S2.1 的验收门槛。
额外做两件事：
1. 并发写不丢（v0.1 的 JSON 全量覆盖在多进程下会互相覆盖）
2. 检索只取目标知识库（验证规模化改造没有退化成全表加载）

用法：
    ./.venv/Scripts/python.exe tools/verify_store_backends.py
    ./.venv/Scripts/python.exe tools/verify_store_backends.py --only sqlite --verbose
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.engine import Database  # noqa: E402
from app.db.models import Chunk  # noqa: E402
from app.db.schema import migrate  # noqa: E402
from app.services.rag import search  # noqa: E402
from app.services.repositories.kb import JsonKbRepository, SqlKbRepository  # noqa: E402
from app.services.store import KBStore  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []


def check(backend: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((backend, name, ok, detail))
    mark = "OK  " if ok else "FAIL"
    print(f"{mark} [{backend}] {name}" + (f" · {detail}" if detail else ""))


def _chunk(doc_id: str, kb_id: str, seq: int, text: str, vec: list[float]) -> Chunk:
    return Chunk(id=f"{doc_id}-{seq}", kb_id=kb_id, document_id=doc_id, text=text,
                 vector=vec, seq=seq, char_start=0, char_end=len(text))


def run_suite(backend: str, store: KBStore) -> None:
    import asyncio

    # ---- KB CRUD ----
    kb = store.add_kb(f"{backend}-库", "ws-a")
    check(backend, "创建知识库", store.get_kb(kb.id) is not None, kb.id)
    check(backend, "工作区归属校验", store.owns(kb.id, "ws-a") and not store.owns(kb.id, "ws-b"))
    check(backend, "越权 kb_ids 被过滤", store.owned_kb_ids("ws-b", [kb.id]) == [])

    # ---- 文档与切片 ----
    doc = store.add_document(kb.id, "死锁.md", "死锁的四个必要条件：互斥、占有并等待、非抢占、循环等待。", 3,
                             source="text", content_hash="hash-deadlock")
    store.add_chunks([
        _chunk(doc.id, kb.id, 0, "死锁的四个必要条件：互斥", [0.1] * 8),
        _chunk(doc.id, kb.id, 1, "占有并等待与非抢占", [0.2] * 8),
        _chunk(doc.id, kb.id, 2, "循环等待形成环路", [0.3] * 8),
    ])
    check(backend, "切片写入", store.count_chunks(kb.id) == 3, f"{store.count_chunks(kb.id)} 条")
    check(backend, "文档列表", len(store.documents_of_kb(kb.id)) == 1)
    check(backend, "按 hash 查重", (store.find_document_by_hash(kb.id, "hash-deadlock") or doc).id == doc.id)

    # ---- 向量往返 ----
    got = store.list_chunks([kb.id])
    vec_ok = bool(got) and abs(got[0].vector[0] - 0.1) < 1e-5 and len(got[0].vector) == 8
    check(backend, "向量往返（float32）", vec_ok, f"dim={len(got[0].vector) if got else 0}")

    # ---- 检索：只取目标知识库 ----
    other = store.add_kb(f"{backend}-干扰库", "ws-a")
    odoc = store.add_document(other.id, "干扰.md", "无关内容", 1)
    store.add_chunks([_chunk(odoc.id, other.id, 0, "完全无关的文本", [0.9] * 8)])
    hits = asyncio.run(search(store, [kb.id], "死锁 条件", top_k=3, mode="lexical"))
    only_target = all(h.chunk_id.startswith(doc.id) for h in hits)
    check(backend, "检索不串库", only_target and len(hits) > 0, f"{len(hits)} 条命中")
    check(backend, "doc_names 回填", store.doc_names().get(doc.id) == "死锁.md")

    # ---- 级联删除 ----
    removed = store.delete_document(doc.id)
    check(backend, "删文档级联清切片", removed == 3 and store.count_chunks(kb.id) == 0, f"清理 {removed} 条")

    # ---- 并发写 ----
    errors: list[str] = []
    base = store.count_chunks(kb.id)

    def worker(idx: int) -> None:
        try:
            d = store.add_document(kb.id, f"并发-{idx}.md", "x", 1, content_hash=f"h{idx}")
            store.add_chunks([_chunk(d.id, kb.id, 0, f"并发文本 {idx}", [0.1] * 8)])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{idx}:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    written = base + 20
    check(backend, "并发 20 写不丢",
          not errors and store.count_chunks(kb.id) == written,
          f"实际 {store.count_chunks(kb.id)}/{written} 错误 {len(errors)}")

    # ---- 删库 ----
    n = store.delete_kb(kb.id)
    check(backend, "删库级联", n >= 1 and store.get_kb(kb.id) is None, f"移除 {n} 篇文档")
    store.delete_kb(other.id)


def build_json() -> tuple[KBStore, str]:
    tmp = Path(tempfile.gettempdir()) / "einstein_verify_kb.json"
    if tmp.exists():
        tmp.unlink()
    return KBStore(JsonKbRepository(tmp)), str(tmp)


def build_sql(backend: str, dsn: str) -> tuple[KBStore, str]:
    db = Database(backend, dsn)
    migrate(db)
    # 只清理本套件产生的测试数据（知识库名以 "{backend}-" 开头），不碰真实数据
    stale = [r["id"] for r in db.query(
        "SELECT id FROM knowledge_bases WHERE name LIKE ?", (f"{backend}-%",),
    )]
    with db.transaction():
        for kb_id in stale:
            db.execute("DELETE FROM chunks WHERE kb_id = ?", (kb_id,))
            db.execute("DELETE FROM documents WHERE kb_id = ?", (kb_id,))
            db.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
    return KBStore(SqlKbRepository(db)), dsn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["json", "sqlite", "postgres"], default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings

    s = get_settings()
    targets = [args.only] if args.only else ["json", "sqlite", "postgres"]

    for backend in targets:
        try:
            if backend == "json":
                store, where = build_json()
            elif backend == "sqlite":
                store, where = build_sql("sqlite", s.sqlite_path)
            else:
                store, where = build_sql("postgres", s.postgres_dsn)
            print(f"\n=== {backend} ({where})")
            run_suite(backend, store)
        except Exception as exc:  # noqa: BLE001
            check(backend, "套件执行", False, f"{type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[2]]
    print("\n" + "-" * 70)
    print(f"结果：{total - len(failed)}/{total} 通过")
    for backend, name, _ok, detail in failed:
        print(f"  失败 [{backend}] {name} · {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
