# -*- coding: utf-8 -*-
"""Query 构造：把评分点变成检索器能精确命中的问题。

设计（呼应 Phase 2 的"短 query 语义更聚焦"）：
- query = 评分点 content + 所需证明材料(evidence_type) + ★ 权重提示；
- 不塞整本标书 → 检索不会被无关词稀释。
"""
from __future__ import annotations

from core.parser.schemas import ScorePoint


def build_query(point: ScorePoint, tender_title: str = "") -> str:
    parts: list[str] = []
    if point.is_star:
        parts.append("关键/实质性条款")
    if point.content:
        parts.append(point.content)
    if point.evidence_type:
        parts.append("需要材料：" + "、".join(point.evidence_type))
    if tender_title:
        parts.append(f"招标项目：{tender_title}")
    return " ".join(parts).strip() or "请描述可交付的能力与方案"
