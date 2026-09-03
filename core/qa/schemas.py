# -*- coding: utf-8 -*-
"""自检质检（QA）数据契约：把 应答草稿 × ★条款 × 数值核对 交叉核对，产出风险清单。

三层防线的收口：
- Phase 3 保证"每条应答有合法引用"；Phase 4 保证"参数数值达标"；
- Phase 5 站在审标人视角，问三件事：★ 点都答了吗(覆盖率)？应答和数值核对打架吗(偏离复核)？
  应答自己跟自己/跟我方能力矛盾吗(数值一致性)？语义风险(张冠李戴/不实)交给 LLM-as-Judge。
每一条 issue 都带严重级 + 出处，供 Phase 7 报告直出"风险清单/待补材料"。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    BLOCK = "block"  # 废标级/硬伤：★未答、★负偏离、自相矛盾承诺
    WARN = "warn"    # 丢分/需注意
    INFO = "info"    # 提示


class IssueKind(str, Enum):
    """白名单：代码判出的 + Judge 判出的，都只能是这里面的类别。"""

    # ---- 代码判（rules.py）----
    UNANSWERED_STAR = "unanswered_star"      # ★ 评分点无有效应答 → BLOCK
    UNANSWERED_POINT = "unanswered_point"    # 普通评分点无有效应答 → WARN
    MATERIAL_GAP = "material_gap"            # 应答自报缺材料（missing_evidence）→ WARN/BLOCK
    STAR_UNDER = "star_under"                # 数值核对：★ 负偏离 → BLOCK
    PARAM_UNDER = "param_under"              # 数值核对：非★ 负偏离 → WARN
    PARAM_UNKNOWN = "param_unknown"          # 数值核对：无数值/找不到可比能力 → WARN(需人工)
    NUM_CONFLICT = "num_conflict"            # 应答正文同主题数值打架（98天 vs 120天）→ WARN
    OVER_COMMIT = "over_commit"              # 应答承诺超出我方语料能力（质保答5年而catalog仅3年）→ BLOCK(★)/WARN
    # ---- Judge 判（judge.py，kind 须在 JUDGE_* 白名单）----
    JUDGE_HALLUCINATION = "judge_hallucination"  # 内容不实/引用不能支撑 → 可改写(有引用时)
    JUDGE_STALE = "judge_stale"              # 张冠李戴/旧标书残留(错误甲方/项目/工期) → 需人工
    JUDGE_OFFTOPIC = "judge_offtopic"        # 答非所问 → 可改写


# 可自动改写的类别（有引用支撑即可重新生成）
FIXABLE_KINDS = {
    IssueKind.JUDGE_HALLUCINATION,
    IssueKind.JUDGE_OFFTOPIC,
    IssueKind.NUM_CONFLICT,
    IssueKind.UNANSWERED_POINT,
    IssueKind.UNANSWERED_STAR,
}

# Judge 能输出的类别白名单（代码对模型结果做防御，防模型发明类别）
JUDGE_KINDS = {
    IssueKind.JUDGE_HALLUCINATION,
    IssueKind.JUDGE_STALE,
    IssueKind.JUDGE_OFFTOPIC,
}


def severity_for_kind(kind: IssueKind) -> IssueSeverity:
    """按类别映射严重级，而不是全信模型的 severity 字段（防对废标级风险降级）。
    Judge 产物里只有 JUDGE_STALE（张冠李戴/旧数据）是废标级；其余 JUDGE_* 一律 WARN。"""
    if kind in (IssueKind.JUDGE_STALE, IssueKind.UNANSWERED_STAR, IssueKind.STAR_UNDER):
        return IssueSeverity.BLOCK
    return IssueSeverity.WARN


class QaIssue(BaseModel):
    id: str
    kind: IssueKind
    severity: IssueSeverity
    point_id: str = Field("", description="关联评分点/应答；无语义时为空串")
    ref: str = Field("", description="出处：证据来源(引用/参数表/语料文件)，用于溯源")
    evidence: str = Field("", description="命中的原文片段")
    reason: str = Field(..., description="为什么是问题、影响")
    fixable: bool = Field(False, description="可否由改写闭环自动处理")


class QaReport(BaseModel):
    issues: list[QaIssue] = Field(default_factory=list)
    block_count: int = 0
    warn_count: int = 0
    info_count: int = 0
    escalation_required: bool = Field(False, description="存在 BLOCK（硬伤），需拦截/升级人工")
    needs_material: list[str] = Field(default_factory=list)  # 全链路并集的待补材料


class QaVerdict(BaseModel):
    """LLM-as-Judge 的返回契约（模型必须按此 schema 输出，且字段要再过代码校验）。

    信任策略：severity 不作为唯一依据 —— service 按 kind 白名单映射严重级
    （JUDGE_STALE→BLOCK，其余 JUDGE_*→WARN），防止模型对废标级风险降级处理。
    """

    point_id: str = Field(..., description="被审的评分点")
    clean: bool = Field(False, description="true=判定无问题（不产生 issue）")
    severity: IssueSeverity = IssueSeverity.INFO
    kind: IssueKind = Field(..., description="出问题时只能在 JUDGE_* 白名单内")
    reason: str = Field("", description="判定理由（非空才有效）")
    suggestion: str = Field("", description="给改写/人工的建议")
    needs_regen: bool = Field(False, description="是否建议打回重写")
