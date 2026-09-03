# -*- coding: utf-8 -*-
"""解析管线端到端测试（Mock LLM，全程离线、确定性）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.parser.pipeline import parse_tender
from llm.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "fixtures" / "tender_sample.pdf"


@pytest.fixture()
def mock_llm(settings):
    return MockProvider(settings)


def test_parse_tender_end_to_end(mock_llm) -> None:
    doc, report = asyncio.run(parse_tender(str(SAMPLE), mock_llm))

    # 头部元数据：确定性正则捞到
    assert doc.tender_title == "智慧园区安防系统升级改造项目"
    assert doc.buyer == "XX市智慧城市建设发展中心"
    assert doc.deadline and doc.deadline.startswith("2026年10月31日")

    # 栏目抽取计数：与 mock fixture(ExtractionResult.json) 一致
    assert len(doc.score_points) == 6
    assert len(doc.star_clauses) == 5
    assert len(doc.tech_params) == 7
    assert len(doc.eligibility) == 4
    assert len(doc.waste_bid_terms) == 6
    assert len(doc.timeline) == 6

    # 结构正确性抽查
    star_rows = [r for r in doc.tech_params if r.star]
    assert [r.param_name for r in star_rows] == ["高清网络摄像机", "AI分析服务器", "平台软件"]
    assert all(c.score is not None for c in doc.score_points)

    # 规则命中 6 栏目，无"命中却抽空"缺口 → 报告 ok
    assert set(report.sections_found) == {
        "score_points", "star_clauses", "tech_params", "eligibility", "waste_bid", "timeline"
    }
    assert report.ok is True
    assert report.skipped_types == []
    assert doc.unparsed_segments == []
    assert report.pages >= 1

    # LLM 只被调用一次，且 fixture/schema 均按 ExtractionResult 路由
    assert len(mock_llm.calls) == 1
    assert mock_llm.calls[0]["schema"] == "ExtractionResult"
    assert mock_llm.calls[0]["fixture"] == "ExtractionResult"


def test_parse_missing_file_raises(mock_llm) -> None:
    """找不到 PDF 必须显式报错，而不是静默返回空文档（fail loud）。"""
    with pytest.raises(FileNotFoundError):
        asyncio.run(parse_tender(str(REPO_ROOT / "fixtures" / "no_such.pdf"), mock_llm))
