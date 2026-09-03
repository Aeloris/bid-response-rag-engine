# -*- coding: utf-8 -*-
"""检索服务：把 向量检索 + BM25 + RRF + 重排 串成一条可观测的混合检索。

retrieve(query) 返回按最终相关性排序的 ScoredChunk 列表；每个结果的 ranks 字段
带 dense/bm25/rrf 各路名次 → 上层可展示"这条为什么被召回"，也便于调试和面试讲解。

依赖注入：Retriever(settings, store, embedding=None)；embedding 缺省按配置取。
BM25 词法索引在首次需要时从 store 全量拉取构建（corpus 小；语料变大再改增量索引）。
"""
from __future__ import annotations

from config.settings import Settings
from core.embeddings import get_embedding_provider
from core.retriever.bm25 import BM25Index
from core.retriever.fusion import rrf_fuse
from core.retriever.rerank import get_reranker
from core.retriever.schemas import ScoredChunk
from core.vector_store import VectorStore


class Retriever:
    def __init__(self, settings: Settings, store: VectorStore, embedding=None) -> None:
        self._settings = settings
        self._store = store
        self._embedding = embedding or get_embedding_provider(settings)
        self._bm25: BM25Index | None = None
        self._doc_meta: dict[int, dict] = {}  # bm25 doc_index -> payload

    def _ensure_bm25(self) -> None:
        if self._bm25 is not None:
            return
        payloads = self._store.all_payloads()
        self._doc_meta = {i: p for i, p in enumerate(payloads)}
        self._bm25 = BM25Index([p.get("text", "") for p in payloads])

    async def retrieve(self, query: str, top_k: int | None = None) -> list[ScoredChunk]:
        cfg = self._settings.retrieval
        top_k = top_k or cfg.rerank_top_n
        self._ensure_bm25()
        assert self._bm25 is not None

        # 1) Dense：query 向量 → qdrant 召回
        (qv,) = await self._embedding.embed([query])
        dense_hits = self._store.search(qv, cfg.dense_top_k)

        # 2) BM25：内存词法索引召回
        bm25_hits = self._bm25.search(query, cfg.bm25_top_k)

        # 统一索引为 chunk_id -> 附带信息
        dense_by_id: dict[str, dict] = {}
        for h in dense_hits:
            h = dict(h)
            dense_by_id[h["chunk_id"]] = h
        dense_order = [h["chunk_id"] for h in dense_hits]

        bm25_by_id: dict[str, dict] = {}
        for doc_idx, score in bm25_hits:
            p = dict(self._doc_meta[doc_idx])
            p["_bm25_score"] = round(score, 4)
            bm25_by_id[p["chunk_id"]] = p
        bm25_order = list(bm25_by_id)

        # 3) RRF 融合（按名次），全候选
        fused = rrf_fuse(dense_order, bm25_order, k=cfg.rrf_k)

        # 4) 重排：融合候选 → rerank_top_n
        candidates = []
        for cid in fused:
            payload = bm25_by_id.get(cid) or dense_by_id[cid]
            candidates.append(payload)
        reranker = get_reranker(self._settings)
        reranked = await reranker.rerank(query, candidates)

        # 记录每路"chunk_id → 名次"供可观测
        pos = lambda lst: {cid: i + 1 for i, cid in enumerate(lst)}  # noqa: E731
        dense_pos, bm25_pos, rrf_pos = pos(dense_order), pos(bm25_order), pos(fused)

        out: list[ScoredChunk] = []
        for rank, payload in enumerate(reranked[:top_k], start=1):
            cid = payload["chunk_id"]
            out.append(
                ScoredChunk(
                    chunk_id=cid,
                    text=payload.get("text", ""),
                    source=payload.get("source", ""),
                    heading=payload.get("heading", ""),
                    heading_path=payload.get("heading_path") or [],
                    final_rank=rank,
                    rerank_score=payload.get("_rerank_score", 0.0),
                    ranks={
                        "dense_rank": dense_pos.get(cid),
                        "dense_score": (dense_by_id.get(cid) or {}).get("_score"),
                        "bm25_rank": bm25_pos.get(cid),
                        "rrf_rank": rrf_pos.get(cid),
                        "rerank_rank": payload.get("_rerank_rank"),
                    },
                )
            )
        return out
