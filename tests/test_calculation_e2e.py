# -*- coding: utf-8 -*-
"""端到端：样例招标书 → 解析 TenderDoc → 抽招标数值要求 → 我方语料能力 → 逐条核对。

全程 Mock、离线确定性。本样例产品能力刻意全部达标：断言"无负偏离、无误报"，
负偏离的捕获逻辑由 tests/test_calculator.py 的对抗用例覆盖。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from config.settings import get_settings
from core.calculator import Calculator, extract
from core.parser.pipeline import parse_tender
from llm.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = REPO_ROOT / "fixtures" / "tender_sample.pdf"
CORPUS = REPO_ROOT / "fixtures" / "corpus"


def _load_corpus_offers() -> list:
    settings = get_settings()
    offers = []
    for f in ["product-guide.md", "qualifications-and-service.md", "cases.md"]:
        text = (CORPUS / f).read_text(encoding="utf-8")
        offers += extract.from_text(text, f)
    return offers


def test_end_to_end_calculation(settings) -> None:
    async def run():
        llm = MockProvider(settings)
        doc, report = await parse_tender(str(SAMPLE_PDF), llm)
        assert report.ok
        reqs = extract.from_tender_doc(doc)
        offers = _load_corpus_offers()
        checks, summary = Calculator(settings).check(reqs, offers)
        return doc, reqs, offers, checks, summary

    doc, reqs, offers, checks, summary = asyncio.run(run())

    # 语料里有可自证的能力，招标侧数值要求被充分解析
    assert len(reqs) >= 12
    assert {o.topic for o in offers}  # 抽到我方能力

    # 每个要求都有一行核对结果
    assert summary.total == len(reqs)
    assert len(checks) == len(reqs)

    # 样例产品刻意全部达标 → 无负偏离、无误报；关键参数(★)都被判满足
    assert summary.conform >= 10
    assert summary.under == 0
    assert summary.star_under == []
    # 抽查具体几行：分辨率、CPU、工期、质保 方向与结论正确
    by_id = {c.req.id: c for c in checks}
    assert by_id["TP-1.1"].verdict.value == "conform"   # 400W 摄像机 → ≥400万
    assert by_id["TP-3.1"].verdict.value == "conform"   # 32核 → ≥32核
    assert by_id["ST-03-1"].verdict.value == "conform"  # 质保 3 年 → ≥3年
    assert by_id["ST-02-1"].verdict.value == "over"     # 工期 98天 < ≤120 天（更优）

    # 出处可溯源：被引用的 offer 都来自语料文件
    cited = {c.offer.source.split(":")[0] for c in checks if c.offer}
    assert cited <= {"product-guide.md", "qualifications-and-service.md"}
