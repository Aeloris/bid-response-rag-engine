# -*- coding: utf-8 -*-
"""Phase 5：自检质检（QA）——把应答草稿 × ★条款 × 数值核对 交叉核对，产出风险清单。

设计（三层"审标人"防线收口）：
- 代码判（rules.py，确定性，离线）：覆盖率（★/普通点漏答、缺材料）、数值核对结论转译
  （★UNDER→废标级）、应答正文自洽与超承诺（98天 vs 120天 / 质保5年而语料仅3年）；
- LLM-as-Judge（judge.py）：只审"有引用且未被代码 BLOCK"的点，查 张冠李戴/旧数据、
  引用不能支撑、答非所问；模型输出过 _validate_verdict 防御（类别白名单/理由非空/clean 置空）；
- 改写闭环（service.regenerate_and_requeue）：可修 issue 打回重写，限次 max_attempts。

对上层暴露：
    QaService(settings, llm).run(points=, answers=, checks=, offers=, ...) -> (QaReport, 应答)
"""
from __future__ import annotations

from config.settings import Settings
from core.qa.schemas import (  # noqa: F401
    IssueKind,
    IssueSeverity,
    QaIssue,
    QaReport,
    QaVerdict,
)
from core.qa.service import QaService

__all__ = [
    "QaService",
    "QaReport",
    "QaIssue",
    "QaVerdict",
    "IssueKind",
    "IssueSeverity",
]


def build_qa(settings: Settings, llm=None) -> QaService:
    return QaService(settings, llm)
