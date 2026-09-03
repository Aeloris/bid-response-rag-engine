# -*- coding: utf-8 -*-
"""数值核对 Schema：把"文字里的数值要求"与"我方能力值"变成可判定的核对行。

设计哲学（组2-B code-interpreter 卖点嫁接）：
- LLM 对数字做的是"下一个 token 预测"，不是算术；≥/≤、单位换算、边界值它常判错且错得自信。
- 所以数值比较必须是**确定性代码**；LLM 只在抽取（措辞 → 四元组）上兜底。
- 判不了就 UNKNOWN（需人工），绝不"觉得对" —— 宁缺毋滥，与 Phase 3 gap 同一哲学。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    CONFORM = "conform"  # 满足要求（含恰在边界）
    OVER = "over"        # 正偏离：超出要求（安全，可作为加分点）
    UNDER = "under"      # 负偏离：达不到要求（★=废标级风险）
    UNKNOWN = "unknown"  # 无法数值比对 → 需人工


class NumericValue(BaseModel):
    """解析后的一个数值条款：已归一化的数值 + 判定用信息。

    约定：
    - value 已折算到基准单位（如 400万像素 → 4000000 像素；3年 → 36 月）。
    - unit  为基准单位名（像素/路/核/芯/天/月/小时/MB/% …），量纲一致才可比较。
    - operator：>= <= > < = None；None 表示原文无数值比较词（如产品规格裸值）。
    - raw 保留原文片段（用于溯源/展示）。
    """

    value: float
    unit: str = Field(..., description="基准单位名（量纲）")
    operator: str | None = Field(None, description=">= <= > < = None")
    raw: str = Field("", description="命中的原文片段，如 '≥400万像素'")


class ParamReq(BaseModel):
    """招标侧的一条数值要求（来自技术参数行或 ★ 数值条款）。"""

    id: str
    label: str = Field(..., description="原始参数名/关键词，如 '分辨率'")
    topic: str = Field(..., description="规范化主题（同义词对齐后），配对用")
    requirement: str = Field(..., description="要求原文（整句，供溯源）")
    numeric: NumericValue | None = Field(None, description="解析出的数值；None=无数值条款")
    star: bool = Field(False, description="是否 ★ 关键参数（负偏离=废标级）")
    source: str = Field("", description="出处，如 参数表第3行 / ST-02")


class OfferClaim(BaseModel):
    """我方侧的能力声明（产品手册/服务承诺语料解析而来）。"""

    id: str
    label: str = Field(..., description="原始关键词，如 '分辨率'")
    topic: str = Field(..., description="规范化主题，与 ParamReq.topic 配对")
    claim: str = Field(..., description="我方声明原文（供溯源/展示）")
    numeric: NumericValue | None = Field(None, description="解析出的数值；None=无数值声明")
    source: str = Field("", description="出处，如 product-guide.md / 语料块")


class ParamCheck(BaseModel):
    """一行核对结果：招标要求 vs 我方能力 vs 判定。"""

    req: ParamReq
    offer: OfferClaim | None = Field(None, description="配对到的我方声明；None=未配对")
    verdict: Verdict
    reason: str = Field(..., description="判定理由（含关键数值与方向）")
    needs_human: bool = Field(False, description="UNKNOWN 或需人工复核时为 True")


class CalcSummary(BaseModel):
    total: int = 0
    conform: int = 0
    over: int = 0
    under: int = 0
    unknown: int = 0
    star_under: list[str] = Field(default_factory=list, description="★ 负偏离清单（最高风险，参数名）")
    needs_human: list[str] = Field(default_factory=list, description="UNKNOWN/未配对清单，待人工确认")


class OfferExtractResult(BaseModel):
    """LLM 兜底抽取的输出契约（仅复杂/无结构措辞时启用）。"""

    claims: list[OfferClaim] = Field(default_factory=list, description="从段落中抽取的我方参数声明")
