# -*- coding: utf-8 -*-
"""Phase 4：数值核对（Calculator）——招标数值要求 × 我方能力 → 偏离判定。

对上层暴露：
    Calculator(settings, llm).check(reqs, offers) -> (list[ParamCheck], CalcSummary)
抽取入口：
    extract.from_tender_doc(TenderDoc)         招标侧 → list[ParamReq]
    extract.from_text(corpus_text, source)     我方语料 → list[OfferClaim]
"""
from __future__ import annotations

from config.settings import Settings
from core.calculator import extract, numeric  # noqa: F401  子模块便捷导入
from core.calculator.schemas import (
    CalcSummary,
    OfferClaim,
    ParamCheck,
    ParamReq,
    Verdict,
)
from core.calculator.service import Calculator

__all__ = [
    "Calculator",
    "extract",
    "numeric",
    "ParamReq",
    "OfferClaim",
    "ParamCheck",
    "CalcSummary",
    "Verdict",
]


def build_calculator(settings: Settings, llm=None) -> Calculator:
    return Calculator(settings, llm)
