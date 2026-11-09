"""从数据库重建向量索引，或用当前嵌入模型重算全部切片向量。

使用场景：
1. 首次从 memory 切到 milvus（集合为空，索引里什么都没有）
2. 索引写失败留下的缺口（`[vector] 索引写入失败` 日志）
3. **换了嵌入模型**（8B→4B，维度 4096→2560）：旧向量与新查询不在同一个
   向量空间，光重建索引没用，必须重新嵌入 → `--reembed`

只改索引时不动业务数据；`--reembed` 会重写 chunks 表的 vector / 维度 / 模型名，
可随时重跑（幂等：同文本同模型算出来的是同一批向量）。

用法：
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py --dry-run
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py --drop --batch 500
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py --reembed --drop
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _reembed_all(batch: int) -> int:
    """用当前嵌入模型重算全部切片向量，并回写模型名与维度。

    分批调 `embed_texts`（它内部还会按条数/字符再切），逐条回写。
    不重切片：切片边界、父子关系、文档版本都不该因为换模型而改变。
    """
    from app.core.config import get_settings
    from app.services.embeddings import embed_texts
    from app.services.store import get_store

    settings = get_settings()
    store = get_store()
    chunks = [c for c in store.list_chunks() if c.text.strip()]
    if not chunks:
        print("库里没有切片，无需重新嵌入")
        return 0

    total = 0
    t0 = time.perf_counter()
    for start in range(0, len(chunks), max(batch, 1)):
        part = chunks[start : start + batch]
        vectors, kind = await embed_texts([c.text for c in part])
        model = settings.embedding_model if kind == "remote" else "mock"
        for chunk, vector in zip(part, vectors, strict=True):
            store.update_chunk(
                chunk.id, text=chunk.text, vector=vector, embedding_kind=kind,
                embedding_model=model, embedding_dim=len(vector),
            )
            total += 1
        done = min(start + batch, len(chunks))
        print(f"  已重嵌入 {done}/{len(chunks)}（{model}，{len(vectors[0])} 维）", flush=True)
    ms = (time.perf_counter() - t0) * 1000
    print(f"重新嵌入完成：{total} 条，耗时 {ms:.0f}ms")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drop", action="store_true", help="先删除集合（换过嵌入模型时必须加）")
    parser.add_argument("--batch", type=int, default=500, help="每批写入/嵌入条数")
    parser.add_argument(
        "--reembed", action="store_true",
        help="用当前嵌入模型重算全部向量（换模型后必须走这个，否则索引里仍是旧空间的向量）",
    )
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.services.store import get_store
    from app.services.vector import reset_vector_index
    from app.services.vector.milvus import MilvusIndex

    settings = get_settings()
    print(f"存储后端：{settings.store_backend} · 向量后端：{settings.vector_backend}")
    print(f"当前嵌入模型：{settings.embedding_model}")

    if args.reembed:
        if args.dry_run:
            print("--dry-run：未重新嵌入（去掉 --dry-run 才会真的重算）")
            return 0
        if not args.drop:
            print(
                "错误：--reembed 必须同时带 --drop。旧集合的维度与新区间不同，"
                "不先删掉会出现「维度冲突」或读写混乱。",
                file=sys.stderr,
            )
            return 2
        asyncio.run(_reembed_all(args.batch))

    chunks = [c for c in get_store().list_chunks() if c.vector]
    dims = {len(c.vector) for c in chunks}
    print(f"库内可索引切片：{len(chunks)} 条 · 维度 {sorted(dims) or '无'}")

    if args.dry_run:
        print("--dry-run：未写入索引")
        return 0
    if not chunks:
        print("没有可索引的切片，退出")
        return 0
    if len(dims) > 1:
        print(f"存在多种维度 {sorted(dims)}，只有与集合维度一致的部分会被写入", file=sys.stderr)

    if settings.vector_backend != "milvus":
        print("当前 vector_backend 不是 milvus，内存实现无需重建（检索时现算）")
        return 0

    reset_vector_index()
    index = MilvusIndex(settings.milvus_uri, settings.milvus_collection)
    if args.drop:
        index.rebuild([])
        print("已删除旧集合")

    t0 = time.perf_counter()
    total = 0
    for start in range(0, len(chunks), max(args.batch, 1)):
        total += index.upsert(chunks[start : start + args.batch])
    ms = (time.perf_counter() - t0) * 1000
    print(f"写入 {total} 条，耗时 {ms:.0f}ms · 集合内现有 {index.count()} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
