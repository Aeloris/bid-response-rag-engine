# -*- coding: utf-8 -*-
"""重排（Rerank）：对"融合后候选"做精排。

为什么需要它：向量/BM25 是**快速但粗糙**的一路打分；Cross-Encoder 把 query 与候选
**拼在一起过同一个模型**，比双塔 embedding 的"独立编码后比相似度"准得多，但贵。
→ 两阶段检索哲学：先宽召回(几十个)再做昂贵的精排(几个)。见 config.retrieval。

Provider 设计同 llm/embedding：Reranker 协议 + 工厂按 provider 切 mock/dashscope。
"""
from __future__ import annotations

from typing import Protocol

from config.settings import Settings
from core.retriever.tokens import tokenize


class Reranker(Protocol):
    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        """对候选（已含 text）按相关性打分降序返回，改写 _rerank_score / _rank。"""
        ...


class MockReranker:
    """离线默认：词法重叠确定性打分（无模型、可复现）。

    用 query token 与候选文本 token 的共现度近似"相关性"——在 keyword 场景是合理站位，
    也让离线冒烟测试有确定性的 top1（真语义重排由 DashScopeReranker 提供，Phase 后续接入）。
    """

    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        q = set(tokenize(query))
        if not q:
            for rank, c in enumerate(candidates, start=1):
                c["_rerank_score"] = 0.0
                c["_rerank_rank"] = rank
            return candidates
        scored = []
        for c in candidates:
            d = set(tokenize(c.get("text", "")))
            overlap = len(q & d)
            # 稍加权长匹配，避免长文档纯靠长度刷分
            score = overlap / max(len(q), 1)
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, c) in enumerate(scored, start=1):
            c["_rerank_score"] = round(score, 6)
            c["_rerank_rank"] = rank
        return [c for _, c in scored]


class DashScopeReranker:
    """真实 Cross-Encoder 重排（gte-rerank-v2）。接入需 DashScope rerank SDK，
    Phase 后续（接通真实 key 联调时）实现；当前构造即说明原因，防止误用。
    """

    def __init__(self, settings: Settings) -> None:  # pragma: no cover
        raise NotImplementedError(
            "DashScopeReranker 尚未接入：需 dashscope SDK 调 gte-rerank-v2。"
            "Phase 2 阶段请保持 config.rerank.provider=mock 离线运行。"
        )

    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        raise NotImplementedError


def get_reranker(settings: Settings) -> Reranker:
    """按 config.rerank.provider 返回 Reranker。非 dashscope 一律 Mock（离线确定）。"""
    if settings.rerank.provider == "dashscope":
        return DashScopeReranker(settings)  # noqa: 目前会 raise，语义为"未接入的明确失败"
    return MockReranker()
