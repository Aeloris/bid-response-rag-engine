# -*- coding: utf-8 -*-
"""解析流水线：PDF → 分页 → 规则章节定位 → LLM 结构化抽取 → 合并为 TenderDoc + ParseReport。

调用约定：parse_tender(pdf_path, llm) -> (TenderDoc, ParseReport)
- llm 只要求有 `async chat(messages, *, schema)`（Protocol 已约束），
  mock / dashscope 可互换 → 测试离线可跑、上线零改动。
- 头部信息（标题/采购人/截止时间）用确定性正则从全文捞，不依赖 LLM，避免关键元数据被幻觉污染。
- LLM 对某栏目返回 0 条、但规则层明明定位到了该栏目 → 判定"抽取缺失"，原文进 unparsed_segments 待人工。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.parser.extract import build_messages
from core.parser.loader import PDFLoader
from core.parser.rules import SectionSpan, find_sections, TYPE_KEYWORDS
from core.parser.schemas import ExtractionResult, TenderDoc

from pydantic import BaseModel, Field


class ParseReport(BaseModel):
    """一次解析的可审计报告（面试可讲：可观测性/失败留痕）。"""

    source_file: str = ""
    pages: int = 0
    sections_found: dict[str, int] = Field(default_factory=dict)  # 栏目 -> 字符数
    extracted_counts: dict[str, int] = Field(default_factory=dict)  # 栏目 -> 抽取条数
    skipped_types: list[str] = Field(default_factory=list)  # 无 LLM 输入的栏目
    ok: bool = True
    errors: list[str] = Field(default_factory=list)


# ---- header 启发式（确定性正则，不经过 LLM）----
_BUYER_RE = re.compile(r"采购人[：:]\s*([^\s（(，,。;；]+)")
_DEADLINE_RE = re.compile(r"投标(?:截止)?时间[：:]?\s*((?:20\d{2}|2026)年\d{1,2}月\d{1,2}日[^，。；\n]*)")

_TITLE_SKIP = re.compile(r"第[一二三四五六七八九十百0-9]+[章节]|招\s*标\s*文\s*件|项目编号|^\s*$")


def _strip_page_artifacts(text: str) -> str:
    """去掉版面噪声：页眉页脚、页码、下划线等，只影响头部启发式提取。"""
    out = []
    for line in text.splitlines():
        s = re.sub(r"[_—\-\s]+$", "", line.strip())
        s = re.sub(r"^\s*[-–—_]*\s*\d{1,3}\s*$", "", s)  # 孤立页码
        if s and not re.fullmatch(r"\d{1,3}", s):
            out.append(s)
    return "\n".join(out)


def _header_from_pages(pages: list[Any]) -> dict[str, str]:
    """从首页/前两页确定性提取 标题/采购人/截止时间。捞不到留空（交给人工/上层兜底）。"""
    head_text = _strip_page_artifacts("\n".join(p.text for p in pages[:2]))
    first_lines = [ln.strip() for ln in head_text.splitlines() if ln.strip()]

    title = ""
    for ln in first_lines[:6]:
        if _TITLE_SKIP.search(ln):
            continue
        # 标题通常含"项目/采购/工程"等词，且不过长
        if 4 < len(ln) < 60 and ("项目" in ln or "采购" in ln or "工程" in ln):
            title = ln
            break
    if not title:
        title = next((ln for ln in first_lines[:6] if not _TITLE_SKIP.search(ln)), "")

    buyer_m = _BUYER_RE.search(head_text)
    deadline_m = _DEADLINE_RE.search(head_text)
    return {
        "tender_title": title,
        "buyer": buyer_m.group(1).strip() if buyer_m else "",
        "deadline": deadline_m.group(1).strip() if deadline_m else "",
    }


async def parse_tender(pdf_path: str | Path, llm: Any, loader: PDFLoader | None = None) -> tuple[TenderDoc, ParseReport]:
    """解析一份招标书 PDF。llm 传入具备 `chat(messages, *, schema)` 的对象。"""
    source = str(pdf_path)
    report = ParseReport(source_file=source)
    loader = loader or PDFLoader()

    pages = loader.load(pdf_path)
    report.pages = len(pages)

    sections: dict[str, SectionSpan] = find_sections(pages)
    report.sections_found = {k: len(v.text) for k, v in sections.items()}

    headers = _header_from_pages(pages)
    doc = TenderDoc(
        source_file=source,
        tender_title=headers["tender_title"],
        buyer=headers["buyer"] or None,
        deadline=headers["deadline"] or None,
    )

    if not sections:
        # 完全没命中：整份文本进待人工，不硬让 LLM 猜
        doc.unparsed_segments.append(PDFLoader.full_text(pages)[:2000])
        report.ok = False
        report.errors.append("规则层未命中任何目标栏目，请检查 rule_anchors 配置")
        return doc, report

    # LLM 通道：栏目摘录 → 结构化抽取
    result = await llm.chat(build_messages(sections), schema=ExtractionResult)
    if not isinstance(result, ExtractionResult):
        report.ok = False
        report.errors.append(f"LLM 未按 schema 返回：{type(result).__name__}")
        return doc, report

    # 合并进 doc
    doc.score_points = result.score_points
    doc.star_clauses = result.star_clauses
    doc.tech_params = result.tech_params
    doc.eligibility = result.eligibility
    doc.waste_bid_terms = result.waste_bid_terms
    doc.timeline = result.timeline

    # 计数 + 缺失检测：规则层定位到了但 LLM 返回空的栏目 → 待人工
    counts = {
        "score_points": len(result.score_points),
        "star_clauses": len(result.star_clauses),
        "tech_params": len(result.tech_params),
        "eligibility": len(result.eligibility),
        "waste_bid_terms": len(result.waste_bid_terms),
        "timeline": len(result.timeline),
    }
    report.extracted_counts = counts

    field_of_type = {
        "score_points": "score_points",
        "star_clauses": "star_clauses",
        "tech_params": "tech_params",
        "eligibility": "eligibility",
        "waste_bid": "waste_bid_terms",
        "timeline": "timeline",
    }
    for type_key in TYPE_KEYWORDS:
        if type_key not in sections:
            continue
        out_field = field_of_type[type_key]
        if counts[out_field] == 0:
            report.skipped_types.append(out_field)
            doc.unparsed_segments.append(sections[type_key].text[:1000])
    report.ok = not report.skipped_types and not report.errors
    return doc, report
