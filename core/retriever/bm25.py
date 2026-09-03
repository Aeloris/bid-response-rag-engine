# -*- coding: utf-8 -*-
"""BM25 词法检索（纯 Python 自实现）。

公式：score(q, d) = Σ_t IDF(t)·(tf·(k1+1))/(tf + k1·(1-b+b·|d|/avgdl))
经典参数 k1=1.5, b=0.75。IDF(t)=ln(1+(N-n_t+0.5)/(n_t+0.5))。

为什么自实现而不是装 rank_bm25：少一个依赖 + 用自研中文分词(tokens.py)可控可讲。
小语料(<几千块)每次查询现算可接受；语料变大再考虑预建索引（Phase 6 优化项）。
"""
from __future__ import annotations

import math
from collections import Counter

from core.retriever.tokens import tokenize

K1 = 1.5
B = 0.75


class BM25Index:
    def __init__(self, docs: list[str]) -> None:
        self._docs = docs
        self._tokenized = [Counter(tokenize(d)) for d in docs]
        self._doc_lens = [sum(c.values()) for c in self._tokenized]
        self._avgdl = sum(self._doc_lens) / len(docs) if docs else 0.0
        self._idf: dict[str, float] = self._compute_idf()

    def _compute_idf(self) -> dict[str, float]:
        n = len(self._docs)
        df: Counter[str] = Counter()
        for c in self._tokenized:
            df.update(c.keys())
        return {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """返回 [(doc_index, score)] 按分降序。"""
        q = Counter(tokenize(query))
        if not q:
            return []
        scores: list[float] = []
        for i, doc_c in enumerate(self._tokenized):
            total = 0.0
            for term, qf in q.items():
                idf = self._idf.get(term)
                if idf is None or idf <= 0:
                    continue
                tf = doc_c.get(term, 0)
                if not tf:
                    continue
                denom = tf + K1 * (1 - B + B * self._doc_lens[i] / self._avgdl) if self._avgdl else tf
                total += idf * ((tf * (K1 + 1)) / denom) * (qf ** 0.5)
            scores.append(total)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(i, scores[i]) for i in ranked[:top_k] if scores[i] > 0]
