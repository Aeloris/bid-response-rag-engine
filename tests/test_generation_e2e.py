# -*- coding: utf-8 -*-
"""端到端：样例招标书 → 解析评分点 → 入库语料 → 逐点检索 → 带引用应答草稿。

全程 Mock（LLM 结构化 + 伪向量 + 词法重排），离线确定性。这是"主链路首尾贯通"的验证。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from config.settings import get_settings
from core.generator import Generator
from core.ingest import ingest_corpus
from core.parser.pipeline import parse_tender
from core.retriever import Retriever
from core.vector_store import VectorStore
from llm.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = REPO_ROOT / "fixtures" / "tender_sample.pdf"
CORPUS = REPO_ROOT / "fixtures" / "corpus"


def test_end_to_end_generation(settings) -> None:
    async def run():
        llm = MockProvider(settings)
        # 解析招标书 → 评分点
        doc, report = await parse_tender(str(SAMPLE_PDF), llm)
        assert report.ok
        # 入库语料 → 检索器
        store = VectorStore(settings.vector_db.collection, settings.embedding.dimension, path=":memory:")
        await ingest_corpus(CORPUS, store, settings)
        retriever = Retriever(settings, store)
        # 生成应答
        gen = Generator(settings, llm)
        answers, summary = await gen.generate(doc.score_points, retriever.retrieve, doc.tender_title)
        return doc, answers, summary, retriever

    doc, answers, summary, retriever = asyncio.run(run())

    # 主链路形状
    assert len(doc.score_points) == 6
    assert summary.total == 6
    assert len(answers) == 6
    assert {a.point_id for a in answers} == {p.id for p in doc.score_points}

    # 应存在已答上的评分点，且带引用（可溯源）
    assert summary.answered >= 1
    answered = [a for a in answers if a.answer.strip()]
    assert answered
    for a in answered:
        assert all(c.source in {"product-guide.md", "cases.md", "qualifications-and-service.md"} for c in a.citations)

    # ★ 条款覆盖情况被记录（样例评分点无★，star_total 应为 0，但机制在位）
    assert summary.star_total == sum(1 for p in doc.score_points if p.is_star)
