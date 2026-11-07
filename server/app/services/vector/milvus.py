"""Milvus 向量索引（HNSW + COSINE）。

与内存实现的差异：
1. 索引是独立状态，必须与数据库保持一致 —— 写入/删除由 KBStore 的删除路径与
   入库路径同步调用，另外首次启动时若集合为空会自动从存储回灌一次。
2. 检索按 kb_id 做标量过滤，不让越权数据进入候选集。
3. Milvus 不可用时抛 VectorIndexError，由检索层降级到 numpy（宁可慢，不可 500）。

集合结构（显式 schema，不用动态字段）：
    id(VARCHAR, pk) | kb_id(VARCHAR) | document_id(VARCHAR) | vector(FLOAT_VECTOR)
"""

from __future__ import annotations

import threading

from app.db.models import Chunk
from app.services.vector.base import VectorIndexError

# 与 memory 实现一致：COSINE 下 distance 就是余弦相似度，越大越近
SEARCH_EF = 64


class MilvusIndex:
    backend = "milvus"

    def __init__(self, uri: str, collection: str, *, bootstrap=None) -> None:  # noqa: ANN001
        self.uri = uri
        self.collection = collection
        self._bootstrap = bootstrap          # bootstrap() -> list[Chunk]
        self._client = None
        self._dim: int | None = None
        self._ready = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 连接与建表
    def _client_or_connect(self):  # noqa: ANN202
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover — 依赖缺失时给明确提示
            raise VectorIndexError("pymilvus 未安装（pip install pymilvus）") from exc
        try:
            self._client = MilvusClient(uri=self.uri)
        except Exception as exc:  # noqa: BLE001
            raise VectorIndexError(f"无法连接 Milvus {self.uri}：{type(exc).__name__}: {exc}") from exc
        return self._client

    def _create(self, client, dim: int) -> None:  # noqa: ANN001
        from pymilvus import DataType

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("kb_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=128)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector", index_type="HNSW", metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        try:
            # 标量倒排索引让 kb_id 过滤走索引而不是全量扫；老版本不支持则忽略
            index_params.add_index(field_name="kb_id", index_type="INVERTED")
        except Exception:  # noqa: BLE001
            pass

        try:
            client.create_collection(
                collection_name=self.collection, schema=schema, index_params=index_params,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorIndexError(f"创建集合失败：{type(exc).__name__}: {exc}") from exc
        self._dim = dim

    def _dimension(self, client) -> int | None:  # noqa: ANN001
        if self._dim:
            return self._dim
        try:
            info = client.describe_collection(self.collection)
            for field in info.get("fields", []):
                if field.get("name") == "vector":
                    params = field.get("params") or {}
                    self._dim = int(params.get("dim") or 0) or None
        except Exception:  # noqa: BLE001 — 拿不到维度不影响检索（Milvus 自己校验）
            pass
        return self._dim

    def _prepare(self, dim: int | None) -> None:
        """首次调用时建集合；集合为空而存储有数据则回灌一次。"""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            client = self._client_or_connect()
            if not client.has_collection(self.collection):
                if not dim:
                    raise VectorIndexError("集合不存在且未提供向量维度，无法建表")
                self._create(client, dim)
            else:
                existing = self._dimension(client)
                if dim and existing and dim != existing:
                    raise VectorIndexError(
                        f"维度冲突：集合 {self.collection} 是 {existing} 维，写入的是 {dim} 维。"
                        "切换嵌入模型后需要重建集合（tools/rebuild_vector_index.py --drop）"
                    )
            self._ready = True
            if self._bootstrap is not None and self.count() == 0:
                rows = self._bootstrap()
                if rows:
                    self._write(rows)
                    print(f"[vector] 集合 {self.collection} 为空，已从存储回灌 {len(rows)} 条", flush=True)

    # ------------------------------------------------------------ 读
    def search(
        self, kb_ids: list[str], vector: list[float], top_k: int,
        *, chunks: list[Chunk] | None = None,
    ) -> list[tuple[str, float]]:
        if not kb_ids or not vector or top_k <= 0:
            return []
        self._prepare(len(vector))
        client = self._client_or_connect()
        clause = ", ".join(_quote(k) for k in kb_ids)
        try:
            result = client.search(
                collection_name=self.collection,
                data=[vector],
                anns_field="vector",
                filter=f"kb_id in [{clause}]",
                limit=top_k,
                output_fields=["document_id"],
                search_params={"metric_type": "COSINE", "params": {"ef": SEARCH_EF}},
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorIndexError(f"Milvus 检索失败：{type(exc).__name__}: {exc}") from exc
        hits = result[0] if result else []
        return [(str(h["id"]), float(h["distance"])) for h in hits]

    def count(self, kb_id: str | None = None) -> int:
        client = self._client_or_connect()
        if not client.has_collection(self.collection):
            return 0
        try:
            rows = client.query(
                collection_name=self.collection,
                filter=f"kb_id == {_quote(kb_id)}" if kb_id else "",
                output_fields=["count(*)"],
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorIndexError(f"Milvus 统计失败：{type(exc).__name__}: {exc}") from exc
        if not rows:
            return 0
        return int(next(iter(rows[0].values())) or 0)

    # ------------------------------------------------------------ 写
    def upsert(self, chunks: list[Chunk]) -> int:
        rows = [c for c in chunks if c.vector]
        if not rows:
            return 0
        self._prepare(len(rows[0].vector))
        return self._write(rows)

    def _write(self, chunks: list[Chunk]) -> int:
        client = self._client_or_connect()
        dim = self._dim or len(chunks[0].vector)
        data = [
            {"id": c.id, "kb_id": c.kb_id, "document_id": c.document_id, "vector": c.vector}
            for c in chunks if len(c.vector) == dim
        ]
        if not data:
            return 0
        try:
            client.upsert(collection_name=self.collection, data=data)
        except Exception as exc:  # noqa: BLE001
            raise VectorIndexError(f"Milvus 写入失败：{type(exc).__name__}: {exc}") from exc
        return len(data)

    def delete_document(self, document_id: str) -> None:
        self._delete(f"document_id == {_quote(document_id)}")

    def delete_kb(self, kb_id: str) -> None:
        self._delete(f"kb_id == {_quote(kb_id)}")

    def _delete(self, expression: str) -> None:
        client = self._client_or_connect()
        if not client.has_collection(self.collection):
            return
        try:
            client.delete(collection_name=self.collection, filter=expression)
        except Exception as exc:  # noqa: BLE001
            raise VectorIndexError(f"Milvus 删除失败：{type(exc).__name__}: {exc}") from exc
        # Milvus 的删除是最终一致（约 1s 后才对查询可见）。删完主动 flush 加速收敛，
        # 但 flush 可能耗时数秒，不能挡在删除请求的返回路径上。
        # 即使窗口期内检索命中已删 id，检索层会用数据库回表结果过滤掉，不会泄漏内容。
        threading.Thread(target=self._flush_quietly, daemon=True).start()

    def _flush_quietly(self) -> None:
        try:
            client = self._client
            if client is not None:
                client.flush(self.collection)
        except Exception:  # noqa: BLE001 — 后台尽力而为
            pass

    def rebuild(self, chunks: list[Chunk] | None = None) -> int:
        rows = chunks if chunks is not None else (self._bootstrap() if self._bootstrap else [])
        client = self._client_or_connect()
        if client.has_collection(self.collection):
            try:
                client.drop_collection(self.collection)
            except Exception as exc:  # noqa: BLE001
                raise VectorIndexError(f"删除集合失败：{type(exc).__name__}: {exc}") from exc
        self._ready = False
        self._dim = None
        if not rows:
            return 0
        self._prepare(len(rows[0].vector))
        written = self._write(rows)
        # 重建是运维动作，同步 flush 一次让统计立刻准确（增量写入不做这步）
        self._flush_quietly()
        return written


def _quote(value: str) -> str:
    """Milvus 过滤表达式里的字符串字面量：双引号包裹并转义。"""
    return '"' + str(value).replace('"', '\\"') + '"'
