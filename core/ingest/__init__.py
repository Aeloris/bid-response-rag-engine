# -*- coding: utf-8 -*-
"""Phase 2：公司语料入库（分块 + 向量化 + 写入向量库，已实现）。

流程：corpus(md/txt) → heading-aware 切块(带章节路径) → embedding(dashscope/mock)
→ VectorStore(qdrant-client 本地模式)。产物可被 core/retriever 检索。
"""
from __future__ import annotations

from config.settings import Settings
from core.ingest.chunker import Chunk, ChunkingConfig, chunk_file, chunk_markdown
from core.ingest.pipeline import IngestReport, ingest_corpus
from core.vector_store import VectorStore

__all__ = [
    "Ingester",
    "Chunk",
    "ChunkingConfig",
    "chunk_file",
    "chunk_markdown",
    "IngestReport",
    "ingest_corpus",
    "VectorStore",
]


class Ingester:
    """门面：把「一份语料目录 → 入库报告」包成可复用对象。"""

    def __init__(self, settings: Settings, store: VectorStore) -> None:
        self._settings = settings
        self._store = store

    async def ingest(self, corpus_dir: str) -> IngestReport:
        return await ingest_corpus(corpus_dir, self._store, self._settings)
