# -*- coding: utf-8 -*-
"""Phase 3：应答生成（打分点 → 带引用的应答草稿，已实现）。

对上层（Phase 5 自检 / Phase 7 报告）暴露：Generator.generate(points, retrieve) → 应答列表 + 汇总。
"""
from __future__ import annotations

from config.settings import Settings
from core.generator.query import build_query
from core.generator.schemas import (
    Citation,
    GenerationSummary,
    PointAnswer,
    TenderReply,
)
from core.generator.service import Generator

__all__ = [
    "Generator",
    "build_query",
    "Citation",
    "PointAnswer",
    "TenderReply",
    "GenerationSummary",
]


def build_generator(settings: Settings, llm) -> Generator:
    return Generator(settings, llm)
