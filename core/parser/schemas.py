# -*- coding: utf-8 -*-
"""解析 Schema：招标书结构化抽取的数据契约。

这是整个项目的"地基类型"：
- Phase 3 生成器按 ScorePoint 逐点应答；
- Phase 4 数值核对按 TechParamRow 逐行判偏离；
- Phase 8 评测以这些字段为准。
字段尽量稳定；确需变更时走版本演进，而非硬改。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScorePoint(BaseModel):
    """一个评分点（评标办法里的得分项）。"""

    id: str = Field(..., description="编号，如 SP-01")
    source: str = Field("", description="原文位置，如 第三章 评标办法")
    content: str = Field(..., description="评分内容/应答要求描述")
    score: int | None = Field(None, description="分值；未知为 None")
    is_star: bool = Field(False, description="是否★实质条款")
    evidence_type: list[str] = Field(
        default_factory=list, description="所需材料类型，如 项目案例/资质证书"
    )


class StarClause(BaseModel):
    """★ 实质性条款：不实质响应即废标。"""

    id: str = Field(..., description="编号，如 ST-01")
    text: str = Field(..., description="条款原文")
    page: int | None = Field(None, description="所在页码")


class TechParamRow(BaseModel):
    """技术参数表的一行（Phase 4 据此判 正/无/负偏离）。"""

    row_no: int = Field(..., description="行号，从 1 开始")
    param_name: str = Field(..., description="参数名")
    requirement: str = Field(..., description="招标要求（含数值/条件）")
    unit: str | None = Field(None, description="量纲/单位")
    star: bool = Field(False, description="是否★ 关键参数（负偏离=废标）")


class TimelineItem(BaseModel):
    """时间节点（保留原文日期串，不强转 date，避免解析歧义）。"""

    event: str = Field(..., description="事项，如 投标截止")
    when: str = Field("", description="原文日期串，如 2026年10月31日 09:30")


class ExtractionResult(BaseModel):
    """单次 LLM 抽取的中间结果：所有可抽取栏目一次返回。

    字段名即抽取出的结构化数据；未提供的栏目保持空列表。
    """

    score_points: list[ScorePoint] = Field(default_factory=list)
    star_clauses: list[StarClause] = Field(default_factory=list)
    tech_params: list[TechParamRow] = Field(default_factory=list)
    eligibility: list[str] = Field(default_factory=list)
    waste_bid_terms: list[str] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)


class TenderDoc(BaseModel):
    """整份招标书解析后的最终结构化对象（对上层唯一入口）。"""

    tender_title: str = Field("", description="项目/文件标题")
    buyer: str | None = Field(None, description="采购人")
    deadline: str | None = Field(None, description="投标截止时间(原文)")
    score_points: list[ScorePoint] = Field(default_factory=list)
    star_clauses: list[StarClause] = Field(default_factory=list)
    tech_params: list[TechParamRow] = Field(default_factory=list)
    eligibility: list[str] = Field(default_factory=list)
    waste_bid_terms: list[str] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)
    source_file: str = Field("", description="来源文件")
    unparsed_segments: list[str] = Field(
        default_factory=list, description="抽取失败/无法识别的原文片段，待人工"
    )
