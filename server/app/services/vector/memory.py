"""内存向量索引：numpy 批量余弦。

v0.1 用 Python 双层循环算余弦（每个切片一次 sqrt），1 万切片单次检索要几百毫秒。
这里改成矩阵乘，向量维度 4096、1 万切片约 40ms（单核），且内存分块处理，
不会一次性把整库矩阵铺开。

没有外部状态：不缓存向量，检索时用调用方传入的候选切片现算，
所以不存在"索引与数据库不同步"的问题（这是 memory 后端最大的优点）。
"""

from __future__ import annotations

import numpy as np

from app.db.models import Chunk

# 一次参与矩阵运算的行数：4096 维 × 1024 行 ≈ 16MB，留出安全余量
BATCH_ROWS = 1024


class MemoryIndex:
    backend = "memory"

    def __init__(self, loader=None) -> None:  # noqa: ANN001 — loader(kb_ids) -> list[Chunk]
        self._loader = loader

    def search(
        self, kb_ids: list[str], vector: list[float], top_k: int,
        *, chunks: list[Chunk] | None = None,
    ) -> list[tuple[str, float]]:
        if not kb_ids or not vector or top_k <= 0:
            return []
        rows = chunks
        if rows is None:
            rows = self._loader(kb_ids) if self._loader else []
        return self.score(rows, vector, top_k)

    @staticmethod
    def score(rows: list[Chunk], vector: list[float], top_k: int) -> list[tuple[str, float]]:
        dim = len(vector)
        usable = [c for c in rows if len(c.vector) == dim]
        if len(usable) != len(rows):
            # 早先这里是静默过滤：换过嵌入模型（4096 → 2560 维）后整库"消失"，
            # hybrid 只剩 BM25，而没人会发现。宁可吵，也要把根因说出来。
            print(
                f"[vector] 维度不匹配丢弃 {len(rows) - len(usable)} 条切片"
                f"（查询 {dim} 维）：换过嵌入模型？请跑 tools/rebuild_vector_index.py 重建",
                flush=True,
            )
        if not usable:
            return []
        query = np.asarray(vector, dtype=np.float32)
        qnorm = float(np.linalg.norm(query)) or 1.0

        scored: list[tuple[str, float]] = []
        for start in range(0, len(usable), BATCH_ROWS):
            part = usable[start : start + BATCH_ROWS]
            matrix = np.asarray([c.vector for c in part], dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1)
            norms[norms == 0] = 1.0
            sims = (matrix @ query) / (norms * qnorm)
            scored.extend(zip([c.id for c in part], sims.tolist(), strict=True))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [(cid, float(score)) for cid, score in scored[:top_k]]

    # ---- 无外部状态：写入/删除/重建都是空操作 ----
    def upsert(self, chunks: list[Chunk]) -> int:
        return 0

    def delete_document(self, document_id: str) -> None:
        return None

    def delete_kb(self, kb_id: str) -> None:
        return None

    def count(self, kb_id: str | None = None) -> int:
        if self._loader is None:
            return 0
        rows = self._loader([kb_id] if kb_id else None)
        return len(rows)

    def rebuild(self, chunks: list[Chunk] | None = None) -> int:
        return 0
