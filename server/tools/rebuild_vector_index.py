"""从数据库重建向量索引。

使用场景：
1. 首次从 memory 切到 milvus（集合为空，索引里什么都没有）
2. 嵌入模型换过、维度变了（老集合必须丢弃重建）
3. 索引写失败留下的缺口（`[vector] 索引写入失败` 日志）

只读数据库、只写索引，不动任何业务数据，可随时重跑。

用法：
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py --dry-run
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py
    ./.venv/Scripts/python.exe tools/rebuild_vector_index.py --drop --batch 500
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drop", action="store_true", help="先删除集合（换过嵌入模型时必须加）")
    parser.add_argument("--batch", type=int, default=500, help="每批写入条数")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.services.store import get_store
    from app.services.vector import reset_vector_index
    from app.services.vector.milvus import MilvusIndex

    settings = get_settings()
    print(f"存储后端：{settings.store_backend} · 向量后端：{settings.vector_backend}")

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
