# -*- coding: utf-8 -*-
"""API 层数据契约：请求/响应的边界模型，与 core/ 强类型模型组装。

分层约定：
- core/ 模型（TenderDoc/GenerationSummary/CalcSummary/QaReport）是"引擎产物"，
  直接嵌进 JobResult —— app 只编排与落盘，不再发明一套平行 DTO（避免双份维护、漂移）。
- 这里只加 API 特有的薄壳：评分点摘要(PointBrief)、任务状态机(JobStatus/JobState)、
  /tenders/parse 的返回(ParseOutcome)、整条标结果(JobResult)。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, computed_field

from core.calculator.schemas import CalcSummary
from core.generator.schemas import GenerationSummary
from core.parser.schemas import TenderDoc
from core.qa.schemas import QaReport


# ------------------------------------------------------------ 评分点摘要
class PointBrief(BaseModel):
    """给前端/人工先看的评分点速览（不拖整段证据）。"""

    id: str
    score: int | None = None
    is_star: bool = False
    content: str = Field("", description="评分内容/应答要求（截断）")
    evidence_type: list[str] = Field(default_factory=list)


def score_points_brief(doc: TenderDoc, limit: int = 400) -> list[PointBrief]:
    out = []
    for p in doc.score_points:
        content = p.content if len(p.content) <= limit else p.content[:limit] + "…"
        out.append(PointBrief(id=p.id, score=p.score, is_star=p.is_star, content=content,
                              evidence_type=p.evidence_type))
    return out


# ------------------------------------------------------------ /tenders/parse 返回
class ParseOutcome(BaseModel):
    ok: bool = True
    source_file: str = ""
    pages: int = 0
    tender_title: str = ""
    buyer: str | None = None
    deadline: str | None = None
    score_points: list[PointBrief] = Field(default_factory=list)
    star_count: int = 0
    param_count: int = 0
    unparsed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def parse_outcome(doc: TenderDoc, report) -> ParseOutcome:
    return ParseOutcome(
        ok=report.ok,
        source_file=doc.source_file,
        pages=getattr(report, "pages", 0),
        tender_title=doc.tender_title,
        buyer=doc.buyer,
        deadline=doc.deadline,
        score_points=score_points_brief(doc),
        star_count=len(doc.star_clauses) + sum(1 for p in doc.score_points if p.is_star),
        param_count=len(doc.tech_params),
        unparsed=doc.unparsed_segments,
        errors=report.errors,
    )


# ------------------------------------------------------------ 任务状态机
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobState(BaseModel):
    job_id: str
    status: JobStatus
    step: str = ""
    error: str = ""
    created_at: str = ""


# ------------------------------------------------------------ 整条标任务结果
class JobResult(BaseModel):
    """一条标跑完的全部产物：gen/calc/qa 三份引擎总结 + 待补材料 + 拦截标记。

    qa.escalation_required=True 表示有 BLOCK（废标级），Phase 7 报告据此置顶拦截。
    """

    job_id: str
    status: JobStatus = JobStatus.DONE
    step: str = "done"
    error: str = ""
    tender_title: str = ""
    score_points: list[PointBrief] = Field(default_factory=list)
    gen: GenerationSummary | None = None
    calc: CalcSummary | None = None
    qa: QaReport | None = None

    @computed_field  # type: ignore[misc]
    @property
    def needs_material(self) -> list[str]:
        if self.qa is not None:
            return self.qa.needs_material
        if self.gen is not None:
            return self.gen.needs_material
        return []

    @computed_field  # type: ignore[misc]
    @property
    def escalation_required(self) -> bool:
        return bool(self.qa is not None and self.qa.escalation_required)
