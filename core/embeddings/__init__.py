# -*- coding: utf-8 -*-
"""Embedding Provider 工厂：按配置切 mock / dashscope。"""
from __future__ import annotations

from config.settings import Settings


def get_embedding_provider(settings: Settings):
    """按 settings.embedding.provider 返回 EmbeddingProvider 实例。"""
    if settings.embedding.provider == "dashscope":
        from core.embeddings.dashscope_embedding import DashScopeEmbedding

        return DashScopeEmbedding(settings)
    from core.embeddings.mock_embedding import MockEmbedding

    return MockEmbedding(dimension=settings.embedding.dimension)


__all__ = ["get_embedding_provider"]
