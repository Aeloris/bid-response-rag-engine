# -*- coding: utf-8 -*-
"""自检质检服务（QaService）：三路代码判 + LLM-as-Judge + 限次改写闭环 → QaReport。

run() 定位在管线"应答生成→数值核对"之后、报告之前：
1. 代码判：覆盖率（★/普通点漏答、缺材料）、数值核对结论转译（★UNDER→BLOCK）、
   应答正文自洽与超承诺（98天 vs 120天 / 质保5年而语料仅3年）——确定性，离线。
2. LLM-as-Judge：只审"有引用且未被代码 BLOCK"的点（judge_all=false 默认），查
   张冠李戴/旧数据、引用不能支撑、答非所问。模型输出再过 _validate_verdict 防御。
3. 改写闭环 regenerate_and_requeue：可修 issue 按评分点聚合，交给外部 rewrite
   （消费方用 Generator 重新生成），限次 max_attempts；成功后剔除该点旧的可修 issue。
BLOCK 存在即 escalation_required=True —— Phase 7 报告要把风险清单置于顶部并拦截投出。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from config.settings import Settings
from core.generator.schemas import PointAnswer
from core.qa import rules
from core.qa.judge import judge_answer
from core.qa.schemas import (
    FIXABLE_KINDS,
    QaIssue,
    QaReport,
    severity_for_kind,
)

# 外部改写器签名：给定评分点与命中的 issue（含证据/理由/建议）→ 返回新应答或 None
RewriteFn = Callable[[str, list[QaIssue]], Awaitable[PointAnswer | None]]


class QaService:
    def __init__(self, settings: Settings, llm=None) -> None:
        self._settings = settings
        self._llm = llm
        self._cfg = settings.qa

    # ------------------------------------------------------------ 主入口
    async def run(
        self,
        *,
        points: list,
        answers: list[PointAnswer],
        checks: list | None = None,
        offers: list | None = None,
        tender_title: str = "",
        buyer: str | None = None,
        deadline: str | None = None,
        contexts_by_point: dict[str, dict[str, str]] | None = None,
        rewrite: RewriteFn | None = None,
    ) -> tuple[QaReport, list[PointAnswer]]:
        """对一份应答包做质检。返回 (QaReport, 终版应答列表[与 points 对齐])。

        不传 checks（Phase4 核对行）则跳过偏离复核；不传 offers 则跳过超承诺比对；
        rewrite 为空则不进入改写闭环（风险留给人工）。全部可选、离线可跑。
        """
        by_id: dict[str, PointAnswer] = {a.point_id: a for a in answers}

        # ---- 1. 代码判（确定性）----
        issues = self._code_issues(points, by_id, checks, offers)

        # ---- 2. LLM-as-Judge（有引用且未被代码 BLOCK 的点）----
        judge_issues = await self._judge_pass(
            points, by_id, issues, tender_title, buyer, deadline, contexts_by_point
        )
        issues = issues + judge_issues

        # ---- 3. 改写闭环（限次；成功则移除该点可修的旧 issue）----
        issues = await self.regenerate_and_requeue(by_id, issues, rewrite)

        final_answers = [by_id.get(p.id) for p in points]
        report = self._report(issues, by_id)
        return report, [a for a in final_answers if a is not None]

    # ------------------------------------------------------------ 代码判汇总
    @staticmethod
    def _code_issues(points, by_id: dict[str, PointAnswer], checks, offers) -> list[QaIssue]:
        return (
            rules.coverage_issues(points, list(by_id.values()))
            + rules.reconcile_deviation(checks or [])
            + rules.numeric_conflicts(list(by_id.values()), offers)
        )

    # ------------------------------------------------------------ Judge 通道
    async def _judge_pass(self, points, by_id, issues, tender_title, buyer, deadline, ctx):
        if self._llm is None:
            return []
        # 候选：answer 非空；默认(judge_all=false)还要有引用；被代码 BLOCK 的点不审
        blocked = {i.point_id for i in issues if i.severity.value == "block" and i.point_id}
        judged: list[QaIssue] = []
        for a in by_id.values():
            if not a.answer.strip():
                continue
            if a.point_id in blocked:
                continue
            if not self._cfg.judge_all and len(a.citations) < self._cfg.min_citations_for_judge:
                continue
            point = next((p for p in points if p.id == a.point_id), None)
            if point is None:
                continue
            verdict = await judge_answer(
                self._llm,
                point,
                a,
                self._cfg,
                tender_title=tender_title,
                buyer=buyer,
                deadline=deadline,
                context_texts=(ctx or {}).get(a.point_id),
            )
            if verdict is None:
                continue
            judged.append(
                QaIssue(
                    id=f"judge-{a.point_id}",
                    kind=verdict.kind,
                    severity=severity_for_kind(verdict.kind),
                    point_id=a.point_id,
                    ref=a.point_id,
                    evidence=(a.answer or "")[:120],
                    reason=f"LLM 复审：{verdict.reason}",
                    fixable=verdict.kind in FIXABLE_KINDS,
                )
            )
        return judged

    # ------------------------------------------------------------ 改写闭环
    async def regenerate_and_requeue(
        self,
        by_id: dict[str, PointAnswer],
        issues: list[QaIssue],
        rewrite: RewriteFn | None,
    ) -> list[QaIssue]:
        """把可修的 issue 按评分点聚合打回重写；成功一次即剔除该点旧的 fixable issue。

        限次 cfg.max_attempts（默认 1 = 只改一轮）。改写成功(内容确实变化)才认定该点已修，
        剔除它身上旧的 fixable 结论；改写器没真改/返回空 → 不剔除、不空转。
        已知局限：本闭环只"清除已修项"，不重跑三路代码判扫描新引入的问题
        （新问题在下次 QA 轮次/Phase 7 报告复核时暴露）——文档如实记录。
        不可修的（张冠李戴 JUDGE_STALE / ★UNDER）永远留在风险清单交人工。
        """
        if rewrite is None or self._cfg.max_attempts <= 0:
            return issues

        live = list(issues)
        attempt = 0
        while attempt < self._cfg.max_attempts:
            attempt += 1
            groups: dict[str, list[QaIssue]] = {}
            for i in live:
                if i.fixable and i.point_id:
                    groups.setdefault(i.point_id, []).append(i)
            if not groups:
                break

            for point_id, feedback in groups.items():
                old = by_id.get(point_id)
                new = await rewrite(point_id, feedback)
                if (
                    new is None
                    or new.point_id != point_id
                    or not new.answer.strip()
                    or (old is not None and new.answer == old.answer)
                ):
                    continue  # 没真改 / 改了但空：不再循环
                by_id[point_id] = new
                # 该点已被成功改写 → 移除它身上的可修 issue（旧的代码判/Judge结论作废）
                live = [i for i in live if not (i.fixable and i.point_id == point_id)]
        return live

    # ------------------------------------------------------------ 汇总
    @staticmethod
    def _report(issues: list[QaIssue], by_id: dict[str, PointAnswer]) -> QaReport:
        material: set[str] = set()
        for a in by_id.values():
            material.update(a.missing_evidence)
        counts = {"block": 0, "warn": 0, "info": 0}
        for i in issues:
            counts[i.severity.value] += 1
        # 稳定排序：BLOCK 在前、同类按 id
        order = {"block": 0, "warn": 1, "info": 2}
        ordered = sorted(issues, key=lambda i: (order[i.severity.value], i.id))
        return QaReport(
            issues=ordered,
            block_count=counts["block"],
            warn_count=counts["warn"],
            info_count=counts["info"],
            escalation_required=counts["block"] > 0,
            needs_material=sorted(material),
        )
