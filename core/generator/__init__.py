"""Phase 3：应答生成（Generator）。

Phase 0 占位。职责预告——
- 输入：评分点要求 + 证据块；
- 输出：pydantic 强约束的结构化应答 {answer, citations[], risk, need_material[]}；
- 走 llm/ 抽象，provider 可为 mock 或 dashscope。
"""


class Generator:
    """评分点要求 + 证据 → 结构化应答初稿。Phase 3 实现。"""
