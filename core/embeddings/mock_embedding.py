# -*- coding: utf-8 -*-
"""MockEmbedding：确定性伪向量。

它不表达"语义"——只是把文本稳定地映射到 D 维空间（哈希 n-gram + 归一化），
保证：同文本 → 同向量；不同文本 → 大概率不同向量（结构正确）。
用途：无 key 时让入库/检索/测试全链路离线跑通；真正语义靠 DashScopeEmbedding。

⚠️ 诚实说明：伪向量的 Dense 检索结果没有语义意义，离线 demo 的命中靠 BM25/词法重排撑住。
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable


class MockEmbedding:
    def __init__(self, dimension: int = 1024, seed: int = 42) -> None:
        self.dimension = dimension

    def _ngrams(self, text: str) -> Iterable[str]:
        """简单 n-gram 特征：CJK/其他按字符滑窗取 1~2 gram，压缩空白。"""
        compact = "".join(text.split()).lower()
        if not compact:
            return []
        grams = list(compact)
        grams += [compact[i : i + 2] for i in range(len(compact) - 1)]
        return grams

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for g in self._ngrams(text):
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]
