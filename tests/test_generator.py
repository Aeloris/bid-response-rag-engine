# -*- coding: utf-8 -*-
"""Generator 单元测试：query 构造 / 空上下文 gap / 引用编号校验 / 模型漏答。"""
from __future__ import annotations

import asyncio

from config.settings import get_settings
from core.generator.query import build_query
from core.generator.schemas import PointAnswer, TenderReply
from core.generator.service import Generator
from core.parser.schemas import ScorePoint
from core.retriever.schemas import ScoredChunk

SP_A = ScorePoint(id="A", content="质保期须≥3年", evidence_type=["售后服务承诺函"], is_star=True)
SP_B = ScorePoint(id="B", content="业绩500万以上", evidence_type=["合同"])

CHUNK = ScoredChunk(
    chunk_id="c1", text="整机质保 3 年，7×24 小时响应。", source="quals.md", heading="售后"
)


class FakeLLM:
    """按场景返回预设 TenderReply。"""

    def __init__(self, answers: list[PointAnswer]) -> None:
        self._answers = answers
        self.calls = 0

    async def chat(self, messages, *, schema=None) -> TenderReply:
        self.calls += 1
        return TenderReply(answers=self._answers)


def run(coro):
    return asyncio.run(coro)


def _gen(fake: FakeLLM) -> Generator:
    return Generator(get_settings(), fake)


def test_build_query_contains_evidence_and_star_marker() -> None:
    q = build_query(SP_A, tender_title="智慧园区安防项目")
    assert "售后" in q and "关键" in q  # evidence_type + is_star 权重词
    assert "智慧园区安防项目" in q
    assert "质保期须" in q


def test_empty_context_point_is_gapped_and_not_sent_to_llm() -> None:
    async def retrieve(query):
        return [] if "业绩" in query else [CHUNK]  # A(质保)有上下文；B(业绩)无

    # LLM 返回 B 的应答（但 B 根本没送——空上下文）→ 应答应被忽略，B 走 gap 而非伪造
    fake = FakeLLM(
        [
            PointAnswer(point_id="A", answer="质保应答", citations=[{"ref": "R1"}]),
            PointAnswer(point_id="B", answer="伪造B的应答", citations=[]),
        ]
    )
    answers, _ = run(_gen(fake).generate([SP_A, SP_B], retrieve, tender_title="智慧园区安防项目"))

    a, b = answers[0], answers[1]
    assert a.point_id == "A" and a.answer  # A 正常生成
    assert b.point_id == "B" and b.answer == "" and b.needs_human
    assert "引用块" in b.note
    assert fake.calls == 1  # 批量只调一次 LLM（空上下文的点不进 prompt）


def test_invalid_citation_ref_is_stripped_and_flags_human() -> None:
    async def retrieve(query):
        return [CHUNK]

    bad = PointAnswer(
        point_id="A",
        answer="质保3年[R1]及某虚构引用[R9]",
        citations=[{"ref": "R9", "chunk_id": "", "source": "", "heading": ""}],
        missing_evidence=[],
    )
    answers, _ = run(_gen(FakeLLM([bad])).generate([SP_A], retrieve))
    a = answers[0]
    assert a.needs_human  # 非法引用 → 需人工
    assert a.citations == []  # 唯一引用 R9 非法被剔除
    assert "剔除" in a.note

    # 合法引用：元数据以索引回填（source 来自上下文，而非模型填的）
    good = PointAnswer(point_id="A", answer="质保3年[R1]", citations=[{"ref": "R1"}])
    answers2, _ = run(_gen(FakeLLM([good])).generate([SP_A], retrieve))
    assert answers2[0].citations[0].source == "quals.md"


def test_llm_missing_requested_point_flagged() -> None:
    async def retrieve(query):
        return [CHUNK]

    fake = FakeLLM([PointAnswer(point_id="A", answer="OK", citations=[{"ref": "R1"}])])
    answers, summary = run(_gen(fake).generate([SP_A, SP_B], retrieve))

    assert answers[1].point_id == "B" and answers[1].needs_human
    assert "未返回" in answers[1].note
    assert summary.total == 2
    assert summary.star_total == 1 and summary.star_answered == 1  # SP_A 是★且已答


def test_prose_out_of_range_citation_flagged_human() -> None:
    """F12 回归：结构化 citations 之外的"正文脚注式 [R#]"越界引用必须被代码拦截。

    上下文只给了 R1，模型却在正文写 [R3] → 不能静默放行；旧逻辑只校验 citations 列表，
    正文里的 [R3] 从不被发现。"""
    async def retrieve(query):
        return [CHUNK]

    ans = PointAnswer(point_id="A", answer="整机质保 3 年[R1]，另援引某外部测试数据[R3]", citations=[])
    out, _ = run(_gen(FakeLLM([ans])).generate([SP_A], retrieve))
    a = out[0]
    assert a.needs_human
    assert "清单外引用 R3" in a.note
    assert a.citations == []

    # 正文只引用合法 R1（citations 列表留空也不该误拦）
    ok = PointAnswer(point_id="A", answer="整机质保 3 年[R1]", citations=[])
    out2, _ = run(_gen(FakeLLM([ok])).generate([SP_A], retrieve))
    assert not out2[0].needs_human
