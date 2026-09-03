# -*- coding: utf-8 -*-
"""向量存储封装（Qdrant）。

关键设计——为什么"本地模式"却叫"存储抽象"：
- 无 Docker 约束下，qdrant-client 的本地模式（QdrantClient(path=...)）是进程内实现，
  但 **API 与正式 Qdrant 服务完全一致**；上生产把构造参数从 path 换成 url 即可，
  上层代码零改动。→ 存储运行方式可插拔（本地=复现/测试，服务器=生产）。
- collection 的向量维度必须与 EmbeddingProvider.dimension 一致（写库/检索都校验）。
- payload 保存原文与元数据：检索命中后直接拿引用原文，不用反查文件。

注入方式：实例化时传入 path（或 url），测试用 tmp_path 保证隔离，不污染 ./data。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from qdrant_client import QdrantClient, models

if TYPE_CHECKING:  # 仅类型注解用；避免 vector_store ↔ ingest 循环导入
    from core.ingest.chunker import Chunk


class VectorStore:
    def __init__(
        self,
        collection: str,
        dimension: int,
        *,
        path: str | Path | None = None,
        url: str | None = None,
    ) -> None:
        if url:
            self._client = QdrantClient(url=url)  # 生产：连 Qdrant 服务器
        elif path == ":memory:" or path is None:
            self._client = QdrantClient(path=":memory:")  # 测试：内存
        else:
            Path(path).mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(path))  # 本地持久化：无 docker
        self.collection = collection
        self.dimension = dimension
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self._client.collection_exists(self.collection):
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.dimension, distance=models.Distance.COSINE
                ),
            )

    def delete_collection(self) -> None:
        if self._client.collection_exists(self.collection):
            self._client.delete_collection(self.collection)

    def reset(self) -> None:
        """清空并按当前维度重建集合（整包重建语义）。"""
        self.delete_collection()
        self._ensure_collection()

    def upsert_chunks(self, chunks: Iterable[Chunk], vectors: list[list[float]]) -> int:
        """批量写入。chunks 与 vectors 同序。

        点 id：qdrant 只接受 uint64 / UUID。这里把内容哈希转成稳定的 uint64
        （chunk_id 为 16 位 hex，int() 后必 < 2^64）→ 内容不变则 id 不变，天然幂等。
        """
        ids = [c.chunk_id for c in chunks]
        if len(ids) != len(vectors):
            raise ValueError(f"chunks({len(ids)}) 与 vectors({len(vectors)}) 数量不一致")
        points = [
            models.PointStruct(
                id=int(c.chunk_id, 16),  # 16 位 hex → uint64
                vector=vec,
                payload={
                    "chunk_id": c.chunk_id,
                    "text": c.text,
                    "source": c.source,
                    "heading": c.heading,
                    "heading_path": c.heading_path,
                },
            )
            for c, vec in zip(chunks, vectors)
        ]
        self._client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def search(self, vector: list[float], top_k: int) -> list[dict[str, Any]]:
        """按向量召回，返回按相似度降序的 payload 列表（附 score 与向量内名次）。"""
        # qdrant-client ≥1.10 用 query_points（旧 .search 已移除）
        res = self._client.query_points(
            collection_name=self.collection, query=vector, limit=top_k
        )
        out: list[dict[str, Any]] = []
        for rank, hit in enumerate(res.points, start=1):
            payload = dict(hit.payload or {})
            payload["_score"] = float(hit.score)
            payload["_dense_rank"] = rank
            out.append(payload)
        return out

    def count(self) -> int:
        return int(self._client.count(collection_name=self.collection, exact=True).count)

    def all_payloads(self) -> list[dict[str, Any]]:
        """全量取出 payload（BM25 需要在内存重建词法索引，语料规模小所以可接受）。"""
        out: list[dict[str, Any]] = []
        # scroll 一次取完：语料 < 几万点，分页留到 Phase 6 优化
        points, _ = self._client.scroll(
            collection_name=self.collection, limit=10_000, with_vectors=False
        )
        for p in points:
            payload = dict(p.payload or {})
            out.append(payload)
        return out
