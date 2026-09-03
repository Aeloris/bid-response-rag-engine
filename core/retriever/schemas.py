# -*- coding: utf-8 -*-
"""检索结果的数据契约。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScoredChunk(BaseModel):
    """一次检索返回的一个引用块（生成器/报告层消费）。"""

    chunk_id: str
    text: str
    source: str
    heading: str = ""
    heading_path: list[str] = Field(default_factory=list)
    final_rank: int = Field(0, description="最终重排后的名次(从1)")
    rerank_score: float = 0.0
    ranks: dict[str, int | float | None] = Field(
        default_factory=dict, description="可观测：dense/bm25/rrf 各路名次与分数"
    )
