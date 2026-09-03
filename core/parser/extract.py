# -*- coding: utf-8 -*-
"""LLM 抽取通道：把规则层定位出的"栏目摘录"交给 LLM，产出结构化 ExtractionResult。

设计要点：
- 输入是 sections（规则层给的确定性栏目），不是整本 PDF → prompt 小、幻觉低、可溯源；
- 用 pydantic schema 做结构化输出（llm.chat(messages, schema=ExtractionResult)），
  由 provider 统一保证"返回即合法 ExtractionResult"（mock 走 fixture，dashscope 走 json_schema 约束）；
- 纯规则抽不到的语义（如"这一条是不是★""需要什么证明文件"）由 LLM 补齐。

数据安全：传出去的只是被定位到的目标栏目文本，且每栏目限长截断（EXCERPT_MAX_CHARS）。
"""
from __future__ import annotations

from core.parser.rules import SectionSpan, TYPE_KEYWORDS

SYSTEM_PROMPT = (
    "你是投标文件结构化解析助手。请严格依据给出的招标文件栏目原文抽取结构化信息，"
    "禁止编造、禁止外推栏目里没有的内容；某个栏目确实缺失时返回对应空数组。"
    "★ 条款判定只看原文是否明确标注★或“实质性”字样。只输出合法 JSON。"
)

EXCERPT_MAX_CHARS = 3000  # 单栏目截断长度，防止正文异常冗长打爆 prompt

# 栏目类型 → prompt 里告诉模型的"该填哪个字段"映射
_FIELD_HINT = {
    "score_points": "score_points：列出每个评分点（因素/分值/评分标准），含evidence_type推断所需证明",
    "star_clauses": "star_clauses：逐条列出全部★实质性条款原文",
    "tech_params": "tech_params：逐行列出技术参数表（参数名/要求/是否★）",
    "eligibility": "eligibility：逐条列出资格要求（资质/业绩/人员/信用）",
    "waste_bid": "waste_bid_terms：逐条列出废标/否决投标情形",
    "timeline": "timeline：列出时间节点（事项+原文日期串）",
}


def build_messages(sections: dict[str, SectionSpan]) -> list[dict]:
    """由栏目摘录构造抽取 prompt。栏目顺序稳定，方便 mock fixture 对拍。"""
    body: list[str] = []
    for field_name in TYPE_KEYWORDS:  # 稳定顺序输出
        span = sections.get(field_name)
        if span is None:
            continue
        snippet = span.text[:EXCERPT_MAX_CHARS]
        hint = _FIELD_HINT.get(field_name, field_name)
        body.append(f"【栏目摘录 · 应填 {hint}】\n{snippet}")
    if not body:
        raise ValueError("无任何可抽取栏目：规则层未命中目标章节")
    user = (
        "下面是从一份招标文件中定位到的若干栏目原文。请按字段抽取结构化数据。\n\n"
        + "\n\n".join(body)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
