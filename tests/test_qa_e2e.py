# -*- coding: utf-8 -*-
"""端到端：样例招标书 → 解析 → 入库 → 逐点生成应答 → 数值核对 → 自检质检(QA)。

全程 Mock、离线确定性。本样例产品能力刻意合规 → 期望 QA 报告：
- BLOCK=0（无废标级硬伤；★ 全覆盖满足、无负偏离、Judge 判 clean）；
- 风险都在 WARN：SP-05 缺盖章承诺函(MATERIAL_GAP)、SP-06 价格分未答(UNANSWERED_POINT)；
- needs_material 汇总待补材料（盖章承诺函 / 报价一览表）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from config.settings import get_settings
from core.calculator import Calculator, extract
from core.generator import Generator
from core.ingest import ingest_corpus
from core.parser.pipeline import parse_tender
from core.qa import QaService
from core.retriever import Retriever
from core.vector_store import VectorStore
from llm.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = REPO_ROOT / "fixtures" / "tender_sample.pdf"
CORPUS = REPO_ROOT / "fixtures" / "corpus"


def _offers():
    settings = get_settings()
    offers = []
    for f in ["product-guide.md", "qualifications-and-service.md", "cases.md"]:
        text = (CORPUS / f).read_text(encoding="utf-8")
        offers += extract.from_text(text, f)
    return offers


def test_end_to_end_qa(settings) -> None:
    async def run():
        llm = MockProvider(settings)
        # ① 解析招标书 → 评分点/★/参数表
        doc, report = await parse_tender(str(SAMPLE_PDF), llm)
        assert report.ok
        # ② 入库语料 → 检索器
        store = VectorStore(settings.vector_db.collection, settings.embedding.dimension, path=":memory:")
        await ingest_corpus(CORPUS, store, settings)
        retriever = Retriever(settings, store)
        # ③ 生成应答（Phase3）
        gen = Generator(settings, llm)
        answers, gsum = await gen.generate(doc.score_points, retriever.retrieve, doc.tender_title)
        # ④ 数值核对（Phase4）：招标数值要求 × 我方语料能力
        reqs = extract.from_tender_doc(doc)
        checks, csum = Calculator(settings).check(reqs, _offers())
        # ⑤ 自检质检（Phase5）
        qa = QaService(settings, llm)
        qrep, final = await qa.run(
            points=doc.score_points,
            answers=answers,
            checks=checks,
            offers=_offers(),
            tender_title=doc.tender_title,
            buyer=doc.buyer,
            deadline=doc.deadline,
        )
        return doc, answers, final, checks, csum, qrep

    doc, answers, final, checks, csum, qrep = asyncio.run(run())

    # ---- 前置链路形状 ----
    assert len(doc.score_points) == 6
    assert len(answers) == 6 and {a.point_id for a in answers} == {p.id for p in doc.score_points}

    # ---- QA：样例合规 → 无废标级硬伤 ----
    assert qrep.block_count == 0
    assert not qrep.escalation_required

    # ---- 三个代码判通道都跑通 ----
    kinds = {i.kind for i in qrep.issues}
    assert IssueKind.MATERIAL_GAP in kinds          # SP-05 缺盖章承诺函
    assert IssueKind.UNANSWERED_POINT in kinds      # SP-06 价格分未答(需人工报价)
    # 数值核对结论转译进报告：conform 不产生 issue，但 Phase4 通道本身参与过
    assert qrep.warn_count >= 2
    assert all(i.severity.value == "warn" for i in qrep.issues)  # 全部 WARN，无 BLOCK

    # ---- needs_material 汇总到报告，供 Phase7 直出"待补材料" ----
    assert "报价一览表" in "".join(qrep.needs_material)
    assert any("盖章" in m or "承诺函" in m for m in qrep.needs_material)

    # ---- Judge 走了 clean 通道（Mock fixture = clean → 不产 issue）----
    assert not any(i.id.startswith("judge-") for i in qrep.issues)

    # ---- 每个评分点仍对齐返回（QA 不改写样例，终版=初版）----
    assert len(final) == 6
    assert {a.point_id for a in final} == {p.id for p in doc.score_points}


from core.qa.schemas import IssueKind  # noqa: E402
