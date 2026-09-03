"""Phase 5：自检/质检（QA）。

Phase 0 占位。职责预告——
- 复用旧数据检测（旧甲方/旧项目名/过期年份，规则+LLM 双检）；
- LLM-as-Judge：判定应答是否实质响应评分点、证据是否支撑结论(grounding)；
- 无依据即拒答/标注；产出漏项与风险清单。
"""


class QualityChecker:
    """对逐条应答做防幻觉/防旧数据自检。Phase 5 实现。"""
