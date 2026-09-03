# -*- coding: utf-8 -*-
"""LLM-as-Judge 的 prompt：让模型当"审标人"逐点复核应答草稿。

Judge 只查代码判不了的**语义风险**，输出受 schema 约束，且结果要再过
judge._validate_verdict 防御（类别白名单/理由非空/clean 置空）：
1. 张冠李戴/旧数据残留：把上一家甲方、旧项目名、过期年份带进了这份标书 —— 最致命；
2. 不实/引用不能支撑：承诺了我方材料里没有的能力，或引用块根本撑不起这句主张；
3. 答非所问：正文没在回应评分点要求（如要求"业绩"却答了"架构"）。

grounding（引用能否支撑正文）需要被审引用块的原文，service 层会按需附上
【被引用材料】；没有材料可核时，Judge 只判 旧数据 与 答非所问（宁缺毋滥）。
"""
from __future__ import annotations

from config.settings import QAConfig
from core.generator.schemas import PointAnswer
from core.parser.schemas import ScorePoint

SYSTEM_PROMPT = (
    "你是投标文件审标专家，负责在**最终盖章投出前**拦截致命错误。只输出合法 JSON。\n"
    "对每个评分点给出的【应答草稿】，逐项检查三类风险，命中就如实上报：\n"
    "1. 旧数据/张冠李戴：草稿里出现与本次招标不符的甲方、项目名、年份、工期、联系人等\n"
    "   （招标项目名、采购人、投标截止会作为【本次招标背景】提供）；发现即 kind=judge_stale，\n"
    "   这是废标级硬伤。\n"
    "2. 不实/引用不能支撑：草稿把【被引用材料】里没有的能力/案例/数字当成事实陈述，\n"
    "   或引用的 R# 材料与主张无关；kind=judge_hallucination。\n"
    "3. 答非所问：草稿正文没有回应评分点的实际要求（要业绩答架构、要参数答流程等）；\n"
    "   kind=judge_offtopic。\n"
    "规则：\n"
    "- 没有证据显示上述问题就 clean=true；不要为了找问题而找问题，小瑕疵不算。\n"
    "- clean=false 时必须给出 kind + 简短 reason（命中原文片段）+ suggestion（怎么改）。\n"
    "- needs_regen=true 仅当你能给出具体修改建议、且重写有把握消除该问题。"
)

# kind 白名单（代码防御在 judge._validate_verdict 再查一遍）
ALLOWED_KINDS = ("judge_stale", "judge_hallucination", "judge_offtopic")


def build_messages(
    point: ScorePoint,
    answer: PointAnswer,
    cfg: QAConfig,
    tender_title: str = "",
    buyer: str | None = None,
    deadline: str | None = None,
    context_texts: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """组装单个评分点的 Judge 消息。context_texts: {ref(R1..): 被引用块原文}。"""
    stars = "是（★ 实质条款）" if point.is_star else "否"
    header = (
        "----- 本次招标背景 -----\n"
        f"招标项目：{tender_title or '（未提供）'}\n"
        f"采购人：{buyer or '（未提供）'}\n"
        f"投标截止：{deadline or '（未提供）'}\n"
        "----- 被审评分点 -----\n"
        f"编号：{point.id}　是否★：{stars}\n"
        f"评分内容/要求：{point.content}\n"
        f"所需证明材料：{'、'.join(point.evidence_type) or '无'}\n"
    )
    cited_block = "（无被引用材料可核）"
    if context_texts:
        cited_block = "\n".join(f"[{k}] {v[:1200]}" for k, v in context_texts.items())
    body = (
        "----- 应答草稿 -----\n"
        f"{answer.answer or '（空应答）'}\n"
        "----- 被引用材料 -----\n"
        f"{cited_block}"
    )
    user = "请按 QaVerdict schema 输出你的判定。\n\n" + header + "\n" + body
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
