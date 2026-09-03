# -*- coding: utf-8 -*-
"""Embedding Provider 协议：与 llm/ 同一套"面向接口"哲学。

业务代码只依赖 `embed(texts) -> list[list[float]]`，不绑死任何模型服务商。
- 无 key（provider=mock）→ MockEmbedding：确定性伪向量，只验证链路、不表达真语义；
- 有 key（provider=dashscope）→ DashScopeEmbedding：text-embedding-v3。
切 provider 时业务代码零改动（同 llm/ 的换模型体验）。
"""
from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """把一批文本编码成向量列表，顺序与输入一致。"""
        ...
