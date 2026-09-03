# -*- coding: utf-8 -*-
"""LLM-as-Judge：对代码判不了的语义风险做终审，并把模型输出过一遍防御校验。

职责：
- 只审"代码放行"的点（有引用、无代码级 BLOCK、answer 非空）；
- 模型输出按 QaVerdict schema 结构化，_validate_verdict 再兜一道：
  - clean=True → 判为无问题（None）；
  - kind 不在 JUDGE_* 白名单 → 丢弃（宁缺毋滥，未知类别不升级）；
  - reason 为空 → 丢弃（没给理由的"问题"不采信）。
judge.py 只产出"这点的判定"，issue 转译/严重级映射/改写闭环在 service.py。
"""
from __future__ import annotations

from config.settings import QAConfig
from core.generator.schemas import PointAnswer
from core.parser.schemas import ScorePoint
from core.qa.prompt import build_messages
from core.qa.schemas import JUDGE_KINDS, QaVerdict


async def judge_answer(
    llm,
    point: ScorePoint,
    answer: PointAnswer,
    cfg: QAConfig,
    tender_title: str = "",
    buyer: str | None = None,
    deadline: str | None = None,
    context_texts: dict[str, str] | None = None,
) -> QaVerdict | None:
    """对单个评分点跑一次 Judge。返回有依据的判定；无问题/模型乱答 → None。"""
    if not answer.answer.strip():
        return None  # 空应答不进 Judge（已由覆盖率代码判负责）
    messages = build_messages(point, answer, cfg, tender_title, buyer, deadline, context_texts)
    raw = await llm.chat(messages, schema=QaVerdict)
    return _validate_verdict(raw, point.id)


def _validate_verdict(raw, point_id: str) -> QaVerdict | None:
    """模型结果过防御：非法/无依据/白名单外 → None，绝不把垃圾升级成 issue。"""
    if not isinstance(raw, QaVerdict):
        return None
    if raw.clean:
        return None
    if raw.kind not in JUDGE_KINDS:
        return None  # 模型发明了白名单外的类别 → 不采信
    if not (raw.reason or "").strip():
        return None
    # 绑定被审点，防模型答错对象
    return raw.model_copy(update={"point_id": point_id})
