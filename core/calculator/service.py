# -*- coding: utf-8 -*-
"""数值核对服务（Calculator）：招标要求 × 我方能力 → 逐行核对清单。

纯确定性核心：本文件不做任何"觉得对"——每一行的判定都来自 compare_numeric 的算术，
找不到可比的量就诚实标 UNKNOWN(需人工)。★ 负偏离单独记账，供 Phase 5 升级为拦截级。
"""
from __future__ import annotations

from collections import defaultdict

from core.calculator.numeric import compare_numeric, same_dimension
from core.calculator.schemas import (
    CalcSummary,
    OfferClaim,
    ParamCheck,
    ParamReq,
    Verdict,
)


class Calculator:
    def __init__(self, settings=None, llm=None) -> None:
        # settings/llm 现阶段占位：核对核心不依赖；llm 为复杂措辞兜底抽取预留
        self._settings = settings
        self._llm = llm

    def check(
        self,
        reqs: list[ParamReq],
        offers: list[OfferClaim],
    ) -> tuple[list[ParamCheck], CalcSummary]:
        offers_by_topic: dict[str, list[OfferClaim]] = defaultdict(list)
        for o in offers:
            offers_by_topic[o.topic].append(o)

        checks: list[ParamCheck] = []
        summary = CalcSummary()
        for req in reqs:
            c = self._check_one(req, offers_by_topic)
            checks.append(c)
            self._tally(summary, c)
        return checks, summary

    # ---- 单行核对 ----
    @staticmethod
    def _check_one(req: ParamReq, offers_by_topic: dict[str, list[OfferClaim]]) -> ParamCheck:
        if req.numeric is None:
            return ParamCheck(
                req=req,
                verdict=Verdict.UNKNOWN,
                reason="该要求无数值条款（或抽取失败），数值核对不适用，需人工确认",
                needs_human=True,
            )
        candidates = [
            o
            for o in offers_by_topic.get(req.topic, [])
            if o.numeric is not None and same_dimension(o.numeric, req.numeric)
        ]
        if not candidates:
            return ParamCheck(
                req=req,
                verdict=Verdict.UNKNOWN,
                reason=f"未找到「{req.topic}」可数值比对的我方声明 → 需人工核对（宁缺毋滥）",
                needs_human=True,
            )

        best = Calculator._pick_best(req, candidates)
        verdict_str, reason = compare_numeric(req.numeric, best.numeric)
        return ParamCheck(
            req=req,
            offer=best,
            verdict=Verdict(verdict_str),
            reason=f"{reason}（引用 {best.source}）",
            needs_human=(verdict_str == Verdict.UNKNOWN.value),
        )

    @staticmethod
    def _pick_best(req: ParamReq, candidates: list[OfferClaim]) -> OfferClaim:
        """从同主题候选里选"最有代表性"的我方能力。

        投标语义：只要存在一个可达标型号，投标人就能用那个型号达标 → 在"达标/更优"
        候选里挑**刚过线**的那个（最近上界，最小虚标）；若都不达标，取能力最强
        （数值最大）者判 UNDER —— 给出"最坏也能声明的值都不够"的结论。
        """
        rn = req.numeric
        if rn is None:
            return candidates[0]

        def is_satisfying(o: OfferClaim) -> bool:
            v, _ = compare_numeric(rn, o.numeric)
            return v in (Verdict.CONFORM.value, Verdict.OVER.value)

        satisfying = [o for o in candidates if is_satisfying(o)]
        if satisfying:
            # 刚过线：对 ≥/=/上限类取最接近要求的最小值
            return min(satisfying, key=lambda o: abs(o.numeric.value - rn.value))
        # 都不达标：取能力最强（数值最大）做"最强声明仍负偏离"的结论
        return max(candidates, key=lambda o: o.numeric.value)

    # ---- 汇总（代码侧计数）----
    @staticmethod
    def _tally(summary: CalcSummary, c: ParamCheck) -> None:
        summary.total += 1
        v = c.verdict
        if v == Verdict.CONFORM:
            summary.conform += 1
        elif v == Verdict.OVER:
            summary.over += 1
        elif v == Verdict.UNDER:
            summary.under += 1
            if c.req.star:
                summary.star_under.append(f"{c.req.id}·{c.req.label}")
        else:
            summary.unknown += 1
            summary.needs_human.append(f"{c.req.id}·{c.req.label}：{c.reason}")
        if c.needs_human and v != Verdict.UNKNOWN:
            summary.needs_human.append(f"{c.req.id}·{c.req.label}：{c.reason}")
