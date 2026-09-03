# -*- coding: utf-8 -*-
"""Phase 1：招标文件结构化解析引擎（规则锚点 + LLM 抽取双通道，已实现）。

流程：PyMuPDF 版面还原 → 规则层章节定位（确定性，含★/废标/参数表等锚点）→
LLM 把定位到的栏目摘录抽取为 pydantic 结构化对象（评分点/★条款/参数表/资格/时间线）→
两路经 schema 校验合并；规则命中但 LLM 抽空 → 原文进"待人工"，绝不静默丢数据。

对上层（Phase 2 入库 / Phase 3 应答）只暴露 TenderDoc 一个入口类型。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.parser.pipeline import ParseReport, parse_tender
from core.parser.schemas import (
    ExtractionResult,
    ScorePoint,
    StarClause,
    TechParamRow,
    TenderDoc,
    TimelineItem,
)

__all__ = [
    "Parser",
    "parse_tender",
    "ParseReport",
    "TenderDoc",
    "ScorePoint",
    "StarClause",
    "TechParamRow",
    "TimelineItem",
    "ExtractionResult",
]


class Parser:
    """门面：把「异步解析单份招标书」包成可复用对象，屏蔽底层模块依赖。"""

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def parse(self, pdf_path: str | Path) -> tuple[TenderDoc, ParseReport]:
        return await parse_tender(pdf_path, self._llm)
