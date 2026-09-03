# -*- coding: utf-8 -*-
"""把"评分点 + 引用块"组装成 LLM 生成 prompt。

幻觉控制第 1、2 层落在这里：
1. 只允许依据给定编号 [R#] 引用块作答（提示词硬约束）；
2. 每条主张后须标 [R#]，且 R# 只能取自本点给定清单 —— 第 3 层（代码校验）在 service 收尾。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config.settings import GeneratorConfig
from core.parser.schemas import ScorePoint

SYSTEM_PROMPT = (
    "你是投标应答撰写助手，为投标评分点撰写可直接给售前审核的应答草稿。\n"
    "规则：\n"
    "1. 只允许依据每个评分点下方提供的【引用块】作答；引用块编号为 [R1]..[Rn]，编号只能取自给定清单。\n"
    "2. 主张事实、数据、案例都必须以 [Rx] 标注来源；没有引用支持的表述不得当成既成事实。\n"
    "3. 若引用块不足以支撑应答（缺材料/证据不足），如实把缺口写进 missing_evidence，"
    "answer 中明确写\"暂无可直接引用的材料/需补充\"，绝不编造公司能力或案例。\n"
    "4. 每个评分点都要覆盖到：承诺能做到的 + 用什么证明(引用)。\n"
    "5. citations 仅列出你在 answer 中实际使用且属于给定清单的编号；covered_evidence 填该点要求中"
    "已被引用材料满足的证明材料项。\n"
    "只输出合法 JSON。"
)


@dataclass
class PreparedPoint:
    """一个"可以送 LLM"的评分点：已带有限数量、截断好的引用块。"""

    point: ScorePoint
    contexts: list[dict] = field(default_factory=list)  # [{ref,text,source,heading,chunk_id}]
    refs: list[str] = field(default_factory=list)


def build_messages(points: list[PreparedPoint], cfg: GeneratorConfig, tender_title: str = "") -> list[dict]:
    """批量生成一条用户消息（一次调用覆盖所有有引用块的评分点）。"""
    blocks: list[str] = []
    for pp in points:
        ctx_lines = "\n".join(
            f"[{c['ref']}] 来源：{c['source']}"
            + (f" / {c['heading']}" if c.get("heading") else "")
            + f"\n{c['text']}"
            for c in pp.contexts
        ) or "（无引用块）"
        star = "是（★）" if pp.point.is_star else "否"
        ev = "、".join(pp.point.evidence_type) or "无"
        blocks.append(
            "----- 评分点 -----\n"
            f"编号：{pp.point.id}\n"
            f"评分内容：{pp.point.content}\n"
            f"所需证明材料：{ev}\n"
            f"是否★：{star}\n"
            f"招标项目：{tender_title}\n"
            f"可用引用块：\n{ctx_lines}"
        )
    user = "请为下列每个评分点撰写应答草稿（按 schema 输出）。\n\n" + "\n\n".join(blocks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
