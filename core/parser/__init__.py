"""Phase 1：招标文件结构化解析引擎（规则锚点 + LLM 抽取双通道）。

Phase 0 占位，暂无实现。职责预告——
- PyMuPDF 还原版面，提取文本与页码；
- 用 config.parser.rule_anchors 做章节定位（规则通道，确定性）；
- 命中区域交给 LLM 抽取为 pydantic 结构化对象（评分点/★条款/参数表/资格/时间线）；
- 两路结果经 schema 校验合并，抽不出的进"待人工"。
"""


class Parser:
    """招标书 → 结构化 TenderDoc 对象。Phase 1 实现。"""
