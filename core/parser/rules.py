# -*- coding: utf-8 -*-
"""规则锚点层：不靠 LLM、纯规则地把 PDF 文本切成"栏目摘录"。

为什么先做规则定位再做 LLM 抽取？
1. 成本/幻觉：直接拿几百页喂 LLM 既贵又易漏；先定位到"评标办法在第三章"，LLM 只需看该章摘录；
2. 可溯源：★ 条款在第几页、来自哪一章，规则层能给出确定性答案；
3. 降载：传给 LLM 的 prompt 从"整本书"降到"几个目标栏目"，每个栏目限长截断。

局限（真实招标书版面五花八门）：
- 本实现用"章节标题行"作为切分边界 + 关键词锚点定栏目。合成样例是一章一栏目，
  真实场景锚点命中率会下降 → 依赖 config.parser.rule_anchors 调参 + Phase5 自检兜底。
- 扫描件（图片 PDF）无文本层，需先 OCR，属已知边界（docs/parser.md 说明）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.parser.loader import Page

# 栏目类型 → 标题锚点关键词（出现在"第X章"标题行即归入该栏目）
# 顺序敏感：★ 放最前，因为含★的标题常同时含"实质性"等词，归给 star_clauses 更合理。
TYPE_KEYWORDS: dict[str, list[str]] = {
    "star_clauses": ["★"],
    "score_points": ["评标办法", "评分办法", "评分标准", "评审因素", "评分细则"],
    "tech_params": ["技术规格", "技术参数", "参数要求", "采购需求一览表", "需求一览表"],
    "eligibility": ["资格要求", "资格条件", "投标人资格"],
    "waste_bid": ["废标", "否决投标", "无效投标"],
    "timeline": ["时间安排", "日程安排", "项目时间"],
}

# 字段名（与 schemas.ExtractionResult / TenderDoc 对齐）→ 人类可读栏目名，用于 prompt
FIELD_LABELS: dict[str, str] = {
    "score_points": "评标办法（评分点）",
    "star_clauses": "实质性条款（★）",
    "tech_params": "技术参数表",
    "eligibility": "资格要求",
    "waste_bid": "废标条款",
    "timeline": "时间安排",
}

_CHAPTER_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇0-9]+\s*[章节]\s*")


@dataclass
class SectionSpan:
    """一段栏目原文。text 从标题行起，到下一章节标题止。"""

    field_name: str
    heading: str
    page_start: int
    text: str = ""
    lines: list[str] = field(default_factory=list)


def is_chapter_heading(line: str) -> bool:
    """仅把"第X章/节 标题"当作栏目边界（其余行都算正文，避免过度切碎参数表）。"""
    return bool(_CHAPTER_RE.match(line))


def classify_heading(heading: str) -> set[str]:
    """一个章节标题命中哪些栏目类型。无命中返回空集（该章不抽取）。"""
    hit: set[str] = set()
    for field_name, keywords in TYPE_KEYWORDS.items():
        if any(k in heading for k in keywords):
            hit.add(field_name)
    return hit


def find_sections(pages: list[Page]) -> dict[str, SectionSpan]:
    """扫描全文，输出 栏目类型 → SectionSpan（同类跨多章时按文档顺序累计）。

    边界规则：
    - 遇到新的"第X章/节"标题行即结束上一个章节的正文收集，转入新章节的命中类型；
    - 一个标题同时命中多类型（如"评标办法及废标条款"）→ **该章正文馈给每个命中类型**，
      避免只喂优先级最高的一个、其余类型得到空 span 导致内容静默丢失；
    - 同一类型在多章出现 → **追加累计**（不"后者覆盖前者"），chapter-to-chapter 间补空行
      分隔，保证文档顺序且不把两章糊成一段；heading/page_start 取该类型首次出现的章节。
    """
    sections: dict[str, SectionSpan] = {}
    active: set[str] = set()  # 当前正在收集正文的章节命中类型
    order = list(TYPE_KEYWORDS)

    def _span(field_name: str) -> SectionSpan:
        sp = sections.get(field_name)
        if sp is None:
            sp = SectionSpan(field_name=field_name, heading="", page_start=0)
            sections[field_name] = sp
        return sp

    for page in pages:
        for raw in page.text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if is_chapter_heading(line):
                # 上一章节正文到此结束：给已累计过正文的活跃栏目补一个空行，与新章分隔
                for t in active:
                    if sections[t].lines:
                        sections[t].lines.append("")
                types = classify_heading(line)
                active = set()
                if types:
                    for field_name in sorted(types, key=order.index):
                        sp = _span(field_name)
                        if sp.heading == "" and sp.page_start == 0:
                            sp.heading = line
                            sp.page_start = page.page_no
                    active = types
                continue
            if active:
                for t in active:
                    _span(t).lines.append(line)

    result: dict[str, SectionSpan] = {}
    for field_name, sp in sections.items():
        while sp.lines and sp.lines[-1] == "":
            sp.lines.pop()  # 去掉尾部因切章产生的空行
        sp.text = "\n".join(sp.lines)
        result[field_name] = sp
    return result
