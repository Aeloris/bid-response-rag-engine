# -*- coding: utf-8 -*-
"""RRF（Reciprocal Rank Fusion）多路结果融合。

RRF(q, d) = Σ_r 1 / (k + rank_r(d))   （r 遍历各检索路，k 通常 60）

为什么用"名次"而不是"分数"融合：
- 向量余弦相似度与 BM25 词频分**量纲/分布完全不同，直接加没有意义**；
- 但"排名第几"在不同检索器间是**可比**的（都是 1..N 的序）。
RRF 因此无需归一化各路分数，且对单路异常高分稳健 —— 这是它在 TREC 等评测里表现稳的原因。
"""
from __future__ import annotations

from collections import defaultdict


def rrf_fuse(*ranked_lists: list[int], k: int = 60) -> list[int]:
    """融合多路（每路为按名次排好的元素 id 列表）→ 全局元素 id 列表（按 RRF 分降序）。

    例：rrf_fuse([3,1,4],[4,1,2]) → 1 在两路都靠前，总分最高。
    """
    acc: dict[int, float] = defaultdict(float)
    for ranking in ranked_lists:
        for rank, item in enumerate(ranking, start=1):
            acc[item] += 1.0 / (k + rank)
    return [item for item, _ in sorted(acc.items(), key=lambda kv: kv[1], reverse=True)]


def rrf_score_of(item: int, *ranked_lists: list[int], k: int = 60) -> float:
    """单个元素在各路的融合分（供可观测/调试）。"""
    score = 0.0
    for ranking in ranked_lists:
        if item in ranking:
            score += 1.0 / (k + ranking.index(item) + 1)
    return round(score, 6)
