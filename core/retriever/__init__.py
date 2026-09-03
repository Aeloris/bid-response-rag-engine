"""Phase 2：混合检索（Retriever）。

Phase 0 占位。职责预告——
- 向量(Dense) + BM25(词法) 双路召回 → RRF 融合 → Rerank 精排；
- 按评分点独立 top-k 检索注入；
- 返回带 来源/章节/页码 元数据的证据块（溯源地基）。
"""


class Retriever:
    """评分点需求 → 证据块列表。Phase 2 实现。"""
