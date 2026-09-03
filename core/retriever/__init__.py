# -*- coding: utf-8 -*-
"""Phase 2：混合检索（Dense + BM25 → RRF 融合 → Rerank 精排，已实现）。

面向上层（Phase 3 生成器）：对每个评分点 query 调 `retriever.retrieve(query)`，
拿到排序好、带出处的引用块列表。
"""
from __future__ import annotations

from config.settings import Settings
from core.retriever.bm25 import BM25Index
from core.retriever.fusion import rrf_fuse
from core.retriever.rerank import MockReranker, get_reranker
from core.retriever.schemas import ScoredChunk
from core.retriever.service import Retriever
from core.retriever.tokens import tokenize
from core.vector_store import VectorStore

__all__ = [
    "Retriever",
    "VectorStore",
    "ScoredChunk",
    "BM25Index",
    "rrf_fuse",
    "get_reranker",
    "MockReranker",
    "tokenize",
]


def build_retriever(settings: Settings, store: VectorStore) -> Retriever:
    """便捷工厂：按配置拼装好可直接 retrieve 的检索器。"""
    return Retriever(settings, store)
