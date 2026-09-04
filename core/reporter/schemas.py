# -*- coding: utf-8 -*-
"""报告层数据契约：把引擎产物（结果/应答/核对/风险）重排成一份给售前审的 BidReport。

核心约定（docs/report.md 概念③ 单一产物源）：
- BidReport 是唯一的"报告真相"，Markdown/HTML/Excel 只是它的三个渲染器 ——
  业务内容只写一遍，导出=换渲染器，杜绝 md/html 各一套逻辑导致的漂移。
- 严重级/判定等枚举**直接复用** core 的类型（IssueSeverity/IssueKind/Verdict/Citation），
  不发明平行枚举，避免双份维护。reporter 是"纯派生视图"，不重跑任何引擎（概念①）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from core.calculator.schemas import Verdict
from core.generator.schemas import Citation
from core.qa.schemas import IssueKind, IssueSeverity


# ------------------------------------------------------------ 头部与一屏结论
class ReportHeader(BaseModel):
    job_id: str
    tender_title: str = ""
    buyer: str | None = None
    deadline: str | None = None
    source_file: str = ""
    generated_at: str = Field("", description="报告生成时间（ISO，build 时写入）")


class ReportVerdict(BaseModel):
    """一屏结论：能不能投 + 三个计数 + 待补材料 + 覆盖/核对摘要。"""

    escalation_required: bool = False
    block_count: int = 0
    warn_count: int = 0
    info_count: int = 0
    needs_material: list[str] = Field(default_factory=list)
    total_points: int = 0
    answered_points: int = 0
    star_total: int = 0
    star_answered: int = 0
    calc_total: int = 0
    calc_conform: int = 0
    calc_over: int = 0
    calc_under: int = 0
    calc_unknown: int = 0


# ------------------------------------------------------------ 逐评分点应答
class ReportPointRisk(BaseModel):
    """评分点被命中的风险（点内展示用的小投影，避免整段 issue 重复）。"""

    kind: IssueKind
    severity: IssueSeverity
    reason: str


class ReportPoint(BaseModel):
    """一个评分点：要求原文 × 我方应答 × 引用 × 命中风险。"""

    point_id: str
    score: int | None = None
    is_star: bool = False
    requirement: str = ""
    evidence_type: list[str] = Field(default_factory=list)
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    note: str = ""
    status: str = "unanswered"  # answered / answered_with_warn / answered_with_block / needs_material / gap / unanswered
    risks: list[ReportPointRisk] = Field(default_factory=list)


# ------------------------------------------------------------ 数值核对明细
class ReportCheck(BaseModel):
    """一行核对：招标要求 vs 我方声明 vs 判定。取自 ParamCheck 展平。"""

    label: str = ""
    topic: str = ""
    star: bool = False
    requirement: str = ""
    offer: str = ""
    offer_source: str = ""
    verdict: Verdict = Verdict.UNKNOWN
    needs_human: bool = False
    reason: str = ""


# ------------------------------------------------------------ 风险清单
class ReportIssue(BaseModel):
    """一条风险（QaIssue 投影，已按严重级排序）。"""

    id: str
    point_id: str = ""
    kind: IssueKind
    severity: IssueSeverity
    reason: str = ""
    ref: str = ""
    evidence: str = ""
    fixable: bool = False


# ------------------------------------------------------------ 整份报告
class BidReport(BaseModel):
    header: ReportHeader
    verdict: ReportVerdict
    points: list[ReportPoint] = Field(default_factory=list)
    checks: list[ReportCheck] = Field(default_factory=list)
    issues: list[ReportIssue] = Field(default_factory=list)
    unparsed: list[str] = Field(default_factory=list)
    missing_artifacts: list[str] = Field(default_factory=list, description="缺哪些步骤产物（报告顶部黄条提示）")
    status: str = Field("done", description="引擎任务状态透传（done/failed/pending…）：failed 不许渲染成可投")
    step: str = Field("done", description="中断步骤（失败时定位到哪一段，如 generate/qa）")
    error: str = Field("", description="失败原因（result.error 透传，报告如实展示）")
