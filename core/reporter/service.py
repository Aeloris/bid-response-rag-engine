# -*- coding: utf-8 -*-
"""报告装配：把已算好的产物重排成 BidReport（纯函数，概念①"绝不重跑引擎"）。

分层约定：
- build_report / build_report_from_artifacts 是**纯函数**：不触 IO、不调 LLM、不重跑引擎，
  喂什么渲染什么 → 单测零 fixture、结果确定、缺料降级不炸。
- 薄 I/O 层 load_job_artifacts（读 data/jobs/{id}/ 的 json 回填成强类型对象）放 **app/artifacts.py**：
  它要解析 result.json 为 app.schemas.JobResult，属 app 层职责；core 层不反向依赖 app。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.qa.schemas import IssueSeverity

from .schemas import (
    BidReport,
    ReportCheck,
    ReportHeader,
    ReportIssue,
    ReportPoint,
    ReportPointRisk,
    ReportVerdict,
)

if TYPE_CHECKING:  # 仅类型标注（避免 core→app 运行时依赖；由 type checker 解析）
    from app.schemas import JobResult


@dataclass
class JobArtifacts:
    """一个 job 工作区的强类型产物包（load_job_artifacts 的返回）。"""

    result: object
    doc: object | None = None
    answers: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


_SEVERITY_ORDER = {
    IssueSeverity.BLOCK: 0,
    IssueSeverity.WARN: 1,
    IssueSeverity.INFO: 2,
}


def _status_for(ans, risk: list[ReportPointRisk]) -> str:
    """一个评分点的展示状态：按 空应答类型 / 命中风险 推导，不依赖模型自报。"""
    if ans is None:
        return "unanswered"
    if not ans.answer.strip():
        return "needs_material" if ans.missing_evidence else "gap"
    if any(r.severity == IssueSeverity.BLOCK for r in risk):
        return "answered_with_block"
    if any(r.severity == IssueSeverity.WARN for r in risk):
        return "answered_with_warn"
    return "answered"


def build_report(
    *,
    result: JobResult,
    doc: object | None = None,
    answers: list | None = None,
    checks: list | None = None,
    missing_artifacts: list[str] | None = None,
) -> BidReport:
    """纯函数：把已算好的产物重排成 BidReport（唯一"报告真相"）。

    容错约定：缺 doc 时用 result.score_points（速览，已截断）兜底点清单；
    answers/checks 可为空；缺哪段记进 missing_artifacts，绝不抛。
    """
    answers = answers or []
    checks = checks or []
    missing_artifacts = missing_artifacts or []

    qa = getattr(result, "qa", None)
    issues = list((qa.issues if qa is not None else None) or [])
    issues_sorted = sorted(issues, key=lambda q: (_SEVERITY_ORDER.get(q.severity, 9), q.id))

    # ---- 逐评分点 ----
    doc_points = getattr(doc, "score_points", None) if doc is not None else None
    points_src = doc_points or getattr(result, "score_points", None) or []
    answers_by = {a.point_id: a for a in answers}
    points: list[ReportPoint] = []
    for src in points_src:
        pid = src.id
        ans = answers_by.get(pid)
        risk = [ReportPointRisk(kind=q.kind, severity=q.severity, reason=q.reason)
                for q in issues_sorted if q.point_id == pid]
        points.append(ReportPoint(
            point_id=pid,
            score=getattr(src, "score", None),
            is_star=bool(getattr(src, "is_star", False)),
            requirement=getattr(src, "content", "") or "",
            evidence_type=list(getattr(src, "evidence_type", None) or []),
            answer=(ans.answer if ans else "") or "",
            citations=list(ans.citations) if ans else [],
            missing_evidence=list(ans.missing_evidence) if ans else [],
            note=(ans.note if ans else "") or "",
            status=_status_for(ans, risk),
            risks=risk,
        ))

    # ---- 数值核对明细 ----
    report_checks: list[ReportCheck] = []
    for ch in checks:
        req = getattr(ch, "req", None)
        offer = getattr(ch, "offer", None)
        report_checks.append(ReportCheck(
            label=(req.label if req else "") or "",
            topic=(req.topic if req else "") or "",
            star=bool(getattr(req, "star", False)) if req else False,
            requirement=(req.requirement if req else "") or "",
            offer=(offer.claim if offer else "") or "",
            offer_source=(offer.source if offer else "") or "",
            verdict=getattr(ch, "verdict", None),
            needs_human=bool(getattr(ch, "needs_human", False)),
            reason=getattr(ch, "reason", "") or "",
        ))

    # ---- 一屏结论 ----
    gen = getattr(result, "gen", None)
    calc = getattr(result, "calc", None)
    verdict = ReportVerdict(
        escalation_required=bool(
            (qa.escalation_required if qa is not None else None)
            or getattr(result, "escalation_required", False)
        ),
        block_count=qa.block_count if qa is not None else 0,
        warn_count=qa.warn_count if qa is not None else 0,
        info_count=qa.info_count if qa is not None else 0,
        needs_material=list(getattr(result, "needs_material", None) or []),
        total_points=len(points),
        answered_points=sum(1 for p in points if p.answer.strip()),
        star_total=sum(1 for p in points if p.is_star),
        star_answered=sum(1 for p in points if p.is_star and p.answer.strip()),
        calc_total=calc.total if calc is not None else 0,
        calc_conform=calc.conform if calc is not None else 0,
        calc_over=calc.over if calc is not None else 0,
        calc_under=calc.under if calc is not None else 0,
        calc_unknown=calc.unknown if calc is not None else 0,
    )
    if gen is not None:  # 代码侧统计优先（模型/生成器算的，代码复核过）
        verdict.answered_points = gen.answered
        verdict.star_total = gen.star_total
        verdict.star_answered = gen.star_answered

    # 引擎状态透传：任务 failed（qa 为空 → block_count=0）时若不管会让报告假绿成"可投"，
    # 必须在渲染层拦成失败态。done/pending 原样带；枚举 → value。
    _status = getattr(result, "status", None)
    status = _status.value if hasattr(_status, "value") else (_status or "done")
    return BidReport(
        header=ReportHeader(
            job_id=result.job_id,
            tender_title=result.tender_title,
            buyer=getattr(doc, "buyer", None) if doc is not None else None,
            deadline=getattr(doc, "deadline", None) if doc is not None else None,
            source_file=getattr(doc, "source_file", "") if doc is not None else "",
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),  # = 本次报告装配时间
        ),
        verdict=verdict,
        points=points,
        checks=report_checks,
        issues=[ReportIssue(
            id=q.id, point_id=q.point_id, kind=q.kind, severity=q.severity,
            reason=q.reason, ref=q.ref, evidence=q.evidence, fixable=q.fixable,
        ) for q in issues_sorted],
        unparsed=list(getattr(doc, "unparsed_segments", None) or []) if doc is not None else [],
        missing_artifacts=list(missing_artifacts),
        status=str(status),
        step=getattr(result, "step", None) or "done",
        error=getattr(result, "error", None) or "",
    )


def build_report_from_artifacts(artifacts: JobArtifacts) -> BidReport:
    """便捷入口：吃 load_job_artifacts 的产物包。"""
    return build_report(
        result=artifacts.result,
        doc=artifacts.doc,
        answers=artifacts.answers,
        checks=artifacts.checks,
        missing_artifacts=artifacts.missing,
    )
