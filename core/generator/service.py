# -*- coding: utf-8 -*-
"""应答生成服务（Generator）。

generate() 流程：
1. 对每个评分点构造 query → retrieve_fn(query) 拿引用块（排序好、带出处）；
2. 引用块为空 → 该点【不送 LLM】，直接标 needs_human=缺口（宁缺毋滥，绝不无中生有）；
3. 有引用块的点 → 限量/截断后批量构造 prompt，一次 schema 生成 TenderReply；
4. 引用编号合法性【代码校验】：模型声称引用的 R# 若不在该点给定清单 → 判需人工并剔除；
   并把 citation 的 source/heading/chunk_id 用引用块真实元数据回填（以索引为准，不信模型文本）；
5. 汇总 GenerationSummary（代码侧统计，不由模型算）。
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from config.settings import Settings
from core.generator.prompt import PreparedPoint, build_messages
from core.generator.query import build_query
from core.generator.schemas import Citation, GenerationSummary, PointAnswer, TenderReply
from core.parser.schemas import ScorePoint
from core.retriever.schemas import ScoredChunk

RetrieveFn = Callable[[str], Awaitable[list[ScoredChunk]]]

# 正文里形如 [R5] 的引用标注（模型很可能在句子末尾带脚注式引用，结构化 citations 之外）
_PROSE_REF = re.compile(r"\[R(\d+)\]")


def _invalid_prose_refs(text: str, legal: set[str]) -> list[str]:
    """扫正文 [R#] 标注，返回不在给定引用清单里的编号（去重、按号排序）。"""
    found: set[str] = set()
    for m in _PROSE_REF.finditer(text or ""):
        ref = f"R{m.group(1)}"
        if ref not in legal:
            found.add(ref)
    return sorted(found, key=lambda s: int(s[1:]))


def _gap_answer(point: ScorePoint, note: str) -> PointAnswer:
    """引用块为空的点：不硬答，标记缺口待人工。"""
    return PointAnswer(
        point_id=point.id,
        answer="",
        missing_evidence=list(point.evidence_type) or [point.content],
        needs_human=True,
        note=note,
    )


class Generator:
    def __init__(self, settings: Settings, llm) -> None:
        self._settings = settings
        self._llm = llm
        self._cfg = settings.generator

    async def generate(
        self,
        points: list[ScorePoint],
        retrieve: RetrieveFn,
        tender_title: str = "",
    ) -> tuple[list[PointAnswer], GenerationSummary]:
        prepared: list[PreparedPoint] = []
        answers: dict[str, PointAnswer] = {}

        # ---- 1&2：逐点检索；空上下文直接 gap ----
        for point in points:
            query = build_query(point, tender_title)
            hits = await retrieve(query)
            if not hits:
                answers[point.id] = _gap_answer(point, note="检索无引用块，未送生成（宁缺毋滥）")
                continue
            pp = PreparedPoint(point=point)
            for i, h in enumerate(hits[: self._cfg.max_contexts], start=1):
                pp.contexts.append(
                    {
                        "ref": f"R{i}",
                        "chunk_id": h.chunk_id,
                        "text": h.text[: self._cfg.max_chars_per_context],
                        "source": h.source,
                        "heading": h.heading,
                    }
                )
                pp.refs.append(f"R{i}")
            prepared.append(pp)

        # ---- 3：批量生成（有引用块的点）----
        if prepared:
            messages = build_messages(prepared, self._cfg, tender_title)
            reply = await self._llm.chat(messages, schema=TenderReply)
            if not isinstance(reply, TenderReply):
                raise TypeError(f"LLM 未按 schema 返回：{type(reply).__name__}")
            provided_by_point = {pp.point.id: pp for pp in prepared}
            seen: set[str] = set()
            for raw in reply.answers:
                pp = provided_by_point.get(raw.point_id)
                if pp is None:  # 模型答了未请求的点，忽略
                    continue
                seen.add(raw.point_id)
                answers[raw.point_id] = self._validate_point_answer(raw, pp)

            # 模型漏答的请求点：标需人工，避免静默缺口
            for pp in prepared:
                if pp.point.id not in seen:
                    answers[pp.point.id] = _gap_answer(
                        pp.point, note="模型未返回该点应答，需人工复核"
                    )

        result = [answers[p.id] for p in points if p.id in answers]
        summary = self._summarize(points, result)
        return result, summary

    # ---- 4：引用编号合法性代码校验 ----
    @staticmethod
    def _validate_point_answer(raw: PointAnswer, pp: PreparedPoint) -> PointAnswer:
        ctx_by_ref = {c["ref"]: c for c in pp.contexts}
        valid: list[Citation] = []
        invalid = 0
        for c in raw.citations:
            ctx = ctx_by_ref.get(c.ref)
            if ctx is None:
                invalid += 1
                continue
            # 回填真实出处（以索引为准，不信模型填写的文字）
            valid.append(
                Citation(
                    ref=c.ref,
                    chunk_id=ctx["chunk_id"],
                    source=ctx["source"],
                    heading=ctx["heading"],
                )
            )
        needs_human = raw.needs_human or invalid > 0 or bool(raw.missing_evidence)
        note_bits = [raw.note] if raw.note else []
        if invalid:
            note_bits.append(f"发现 {invalid} 条引用编号不在给定清单，已剔除")
        # 正文脚注式 [R#]：结构化清单之外还可能"句子尾巴带引用"，清单外的必须标需人工
        prose_bad = _invalid_prose_refs(raw.answer, set(ctx_by_ref))
        if prose_bad:
            needs_human = True
            note_bits.append("正文含清单外引用 " + "、".join(prose_bad) + "，已标需人工")
        return PointAnswer(
            point_id=raw.point_id,
            answer=raw.answer,
            citations=valid,
            covered_evidence=raw.covered_evidence,
            missing_evidence=raw.missing_evidence,
            needs_human=needs_human,
            note="；".join(note_bits),
        )

    # ---- 5：代码侧汇总 ----
    @staticmethod
    def _summarize(points: list[ScorePoint], answers: list[PointAnswer]) -> GenerationSummary:
        by_id = {a.point_id: a for a in answers}
        s = GenerationSummary(total=len(points))
        material: set[str] = set()
        for p in points:
            a = by_id.get(p.id)
            is_star = bool(p.is_star)
            if is_star:
                s.star_total += 1
            if a is None:
                s.needs_human_count += 1
                continue
            if a.answer.strip():
                s.answered += 1
                if is_star:
                    s.star_answered += 1
            if not a.citations:
                s.empty_context += 1  # 无有效引用（含未送LLM的gap）
            if a.needs_human:
                s.needs_human_count += 1
            material.update(a.missing_evidence)
        s.needs_material = sorted(material)
        return s
