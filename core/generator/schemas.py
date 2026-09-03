# -*- coding: utf-8 -*-
"""应答生成的数据契约。

- Citation / PointAnswer / TenderReply 是"LLM 也要按此 schema 产出"的模型侧类型（结构化输出）；
- GenerationSummary 是代码侧统计，不由模型生成（模型算数不可信，代码算）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """应答中的一条引用（指向某个引用块，可点回公司语料原文）。"""

    ref: str = Field(..., description="编号，如 R1（必须取自生成器给出的清单）")
    chunk_id: str = ""
    source: str = ""
    heading: str = ""


class PointAnswer(BaseModel):
    """一个评分点的应答单元。"""

    point_id: str
    answer: str = Field("", description="应答正文(Markdown，主张后以 [R1] 标注)")
    citations: list[Citation] = Field(default_factory=list)
    covered_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    needs_human: bool = Field(False, description="是否有缺口/低置信，需售前人工复核")
    note: str = Field("", description="机器可读的说明（缺口原因/校验失败原因）")


class TenderReply(BaseModel):
    """一份招标书所有评分点的批量应答（LLM 返回）。"""

    answers: list[PointAnswer] = Field(default_factory=list)


class GenerationSummary(BaseModel):
    """代码侧统计，供报告/人工复核使用。"""

    total: int = 0
    answered: int = 0            # answer 非空
    empty_context: int = 0       # 检索不到引用块，未送 LLM 直接标缺
    needs_material: list[str] = Field(default_factory=list)  # 全部 missing_evidence 去重
    needs_human_count: int = 0
    star_total: int = 0
    star_answered: int = 0
