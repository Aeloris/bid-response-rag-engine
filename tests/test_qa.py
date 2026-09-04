# -*- coding: utf-8 -*-
"""Phase 5 单测：三路代码判、Judge 防御、QaService 主流程与改写闭环。

离线确定性：纯规则 + Mock/stub LLM。分四组：
1. coverage_issues        ★/普通点漏答、缺材料 的严重级与类别
2. reconcile_deviation    Phase4 核对结论 → BLOCK/WARN 转译
3. numeric_conflicts      自相矛盾 / 超承诺 / 弱承诺与噪音不误报
4. judge + QaService      防御校验、clean 置空、改写闭环限次、escalation
"""
from __future__ import annotations

import asyncio

import pytest

from config.settings import get_settings
from core.calculator.schemas import NumericValue, OfferClaim, ParamCheck, ParamReq, Verdict
from core.generator.schemas import PointAnswer
from core.parser.schemas import ScorePoint
from core.qa import QaService, rules
from core.qa.judge import _validate_verdict, judge_answer
from core.qa.schemas import (
    FIXABLE_KINDS,
    IssueKind,
    IssueSeverity,
    QaIssue,
    QaVerdict,
)
from llm.mock_provider import MockProvider


def _pt(id_, content="应答要求", star=False, ev=None) -> ScorePoint:
    return ScorePoint(id=id_, content=content, is_star=star, evidence_type=ev or [])


def _ans(point_id, text="", citations=(), missing=(), needs_human=False) -> PointAnswer:
    return PointAnswer(
        point_id=point_id,
        answer=text,
        citations=list(citations),
        missing_evidence=list(missing),
        needs_human=needs_human,
    )


def _off(topic, value, unit, claim="能力") -> OfferClaim:
    return OfferClaim(id="o", label=topic, topic=topic, claim=claim,
                      numeric=NumericValue(value=value, unit=unit))


def _req(req_id, topic, requirement, star=False, op=">=", value=0.0, unit="") -> ParamReq:
    return ParamReq(
        id=req_id, label=topic, topic=topic, requirement=requirement,
        numeric=NumericValue(value=value, unit=unit, operator=op, raw=requirement),
        star=star, source="参数表",
    )


# ================================================================ 1. coverage


class TestCoverage:
    def test_star_unanswered_is_block(self):
        pts = [_pt("SP-01", star=True)]
        issues = rules.coverage_issues(pts, [])
        assert issues[0].kind == IssueKind.UNANSWERED_STAR
        assert issues[0].severity == IssueSeverity.BLOCK

    def test_plain_unanswered_is_warn(self):
        issues = rules.coverage_issues([_pt("SP-01")], [])
        assert issues[0].kind == IssueKind.UNANSWERED_POINT
        assert issues[0].severity == IssueSeverity.WARN

    def test_answered_with_missing_material(self):
        pts = [_pt("SP-05")]
        ans = [_ans("SP-05", text="提供整机质保 3 年", missing=["售后服务承诺函（盖章原件）"])]
        issues = rules.coverage_issues(pts, ans)
        kinds = {i.kind for i in issues}
        assert IssueKind.MATERIAL_GAP in kinds
        assert all(i.severity == IssueSeverity.WARN for i in issues)  # 非★=WARN

    def test_star_answered_missing_material_escalates(self):
        pts = [_pt("SP-05", star=True)]
        ans = [_ans("SP-05", text="实质响应", missing=["盖章承诺函"])]
        issues = rules.coverage_issues(pts, ans)
        assert issues[0].severity == IssueSeverity.BLOCK

    def test_full_answer_clean(self):
        pts = [_pt("SP-01")]
        ans = [_ans("SP-01", text="已应答 [R1]", citations=[{"ref": "R1"}])]
        assert rules.coverage_issues(pts, ans) == []


# ================================================================ 2. reconcile


class TestReconcile:
    def _check(self, req: ParamReq, verdict: Verdict) -> ParamCheck:
        return ParamCheck(req=req, verdict=verdict, reason="r")

    def test_star_under_to_block(self):
        c = self._check(_req("ST-1", "分辨率", "≥400万像素", star=True, value=4e6, unit="像素"), Verdict.UNDER)
        issues = rules.reconcile_deviation([c])
        assert issues[0].kind == IssueKind.STAR_UNDER
        assert issues[0].severity == IssueSeverity.BLOCK

    def test_plain_under_to_warn(self):
        c = self._check(_req("TP-1", "分辨率", "≥400万", value=4e6, unit="像素"), Verdict.UNDER)
        issues = rules.reconcile_deviation([c])
        assert issues[0].kind == IssueKind.PARAM_UNDER
        assert issues[0].severity == IssueSeverity.WARN

    def test_unknown_to_warn(self):
        c = self._check(_req("TP-2", "光缆芯数", "≥24芯", value=24, unit="芯"), Verdict.UNKNOWN)
        issues = rules.reconcile_deviation([c])
        assert issues[0].kind == IssueKind.PARAM_UNKNOWN
        assert issues[0].severity == IssueSeverity.WARN

    def test_conform_over_silent(self):
        ok = [self._check(_req("TP-1", "分辨率", "≥400万", value=4e6, unit="像素"), Verdict.CONFORM),
              self._check(_req("ST-2", "工期", "≤120天", star=True, value=120, unit="天"), Verdict.OVER)]
        assert rules.reconcile_deviation(ok) == []


# ================================================================ 3. numeric_conflicts


class TestNumericConflicts:
    def test_contradictory_plain_values(self):
        # "质保 5 年" 与 "质保 1 年" 同主题同量纲、不可能同时成立
        ans = [_ans("SP-05", "整机质保 5 年，质保 1 年")]
        issues = rules.numeric_conflicts(ans, [])
        assert any(i.kind == IssueKind.NUM_CONFLICT for i in issues)
        assert issues[0].evidence == "5年 vs 1年"

    def test_spaced_numbers_still_detected(self):
        # 数字与单位带空格（"98 日历天"）不影响同句多数值推进
        ans = [_ans("SP-04", "工期 98 日历天，交付周期不少于 120 日历天")]
        issues = rules.numeric_conflicts(ans, [])
        assert any(i.kind == IssueKind.NUM_CONFLICT for i in issues)

    def test_non_contradictory_silent(self):
        # 98 天工期 ⊂ ≤120 天交付上限 → 可共存，不误报
        ans = [_ans("SP-04", "我方工期 98 日历天，承诺交付不晚于 120 日历天")]
        assert rules.numeric_conflicts(ans, []) == []

    def test_over_commit_beyond_catalog(self):
        ans = [_ans("SP-05", "我方质保期 5 年")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT

    def test_commit_at_cap_is_ok(self):
        ans = [_ans("SP-05", "质保期 3 年")]
        offers = [_off("质保期", 36, "月", "质保3年")]
        assert rules.numeric_conflicts(ans, offers) == []

    def test_lower_better_direction(self):
        # 到场时效越小越优：承诺 1 小时 < 我方能力下限 2 小时 → 超承诺
        ans = [_ans("SP-05", "重大故障 1 小时内到场")]
        offers = [_off("到场时间", 2, "小时", "2小时内到场")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT

    def test_hedge_not_conflict(self):
        # "可延保至 5 年" 是弱承诺，不当作与 3 年矛盾的硬承诺
        ans = [_ans("SP-05", "整机质保 3 年并可延保至 5 年")]
        offers = [_off("质保期", 36, "月", "质保3年")]
        assert rules.numeric_conflicts(ans, offers) == []

    def test_optional_extension_offer_does_not_raise_cap(self):
        """OVER_COMMIT 回归：offer 语料"整机质保3年，可延保至5年"的 5 年是**可选项**，
        不能把能力上限抬到 60 而放走"承诺 4 年"(48 > 硬能力 36) 的超承诺。"""
        offers = [_off("质保期", 36, "月", "整机质保3年"),
                  _off("质保期", 60, "月", "可延保至5年")]
        issues = rules.numeric_conflicts([_ans("SP-05", "我方质保期 4 年")], offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT
        # 直白承诺到硬能力上限 3 年仍干净；直白承诺 5 年（硬）超硬能力 36 → 照拦
        assert rules.numeric_conflicts([_ans("SP-05", "质保期 3 年")], offers) == []
        bad = rules.numeric_conflicts([_ans("SP-05", "我方质保期 5 年")], offers)
        assert bad and bad[0].kind == IssueKind.OVER_COMMIT

    def test_noise_and_empty_silent(self):
        ans = [_ans("SP-01", "符合 GB/T28181 对接，支持 7×24 小时运行"),
               _ans("SP-06", "")]
        assert rules.numeric_conflicts(ans, []) == []

    def test_bare_ke_commit_not_hedged(self):
        """回归 H1：裸"可"绝不能当弱承诺词。"我方可承诺交付工期 70 日历天"是真硬承诺，
        若被 hedge 吞掉会跳过 → OVER_COMMIT（承诺 70 < 我方能力下限 90）漏检。"""
        ans = [_ans("SP-05", "我方可承诺交付工期 70 日历天")]
        offers = [_off("工期", 90, "天", "标准交付工期 90 天")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT
        assert "≤90天" in issues[0].reason          # bound 带规范单位（M1 口径）

    def test_memory_capacity_not_lumped_to_storage(self):
        """回归 H2：短别名"容量"曾把"内存容量"归到 存储容量，与 内存 能力池对不齐 → 超卖漏检。
        语料同时有 存储容量 120TB 时更会放走 512GB 内存超卖。"""
        ans = [_ans("SP-06", "我方服务器内存容量 512GB")]
        offers = [_off("内存", 262144, "MB", "单机内存 256GB"),    # parse 统一到 MB：256GB
                  _off("存储容量", 125829120, "MB", "存储 120TB")]  # 120TB
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT
        assert issues[0].evidence == "512GB"

    def test_cross_unit_minutes_hours_over_commit(self):
        """回归 M1：应答"30 分钟内" vs 语料"2 小时内"曾因 分/时 量纲不同对不齐 → 超承诺漏检。
        现在统一折到分钟：30 < 120（到场时间越小越优）→ 拦。"""
        ans = [_ans("SP-05", "重大故障 30 分钟内到场")]
        offers = [_off("到场时间", 2, "小时", "2小时内到场")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT
        assert "≤120分钟" in issues[0].reason         # bound 文案带规范单位，不是裸 120


# ================================================================ 4. judge + service


class _FakeLLM:
    """按点返回预置 QaVerdict 的 stub（测 judge/service，不依赖 fixture 文件）。"""

    def __init__(self, verdicts: dict[str, QaVerdict | None]) -> None:
        self._v = verdicts
        self.calls = 0

    async def chat(self, messages, *, schema=None):
        self.calls += 1
        # 从 user 消息里取 point_id（简化定位）
        import re
        m = re.search(r"编号：(SP-\d+)", messages[1]["content"])
        pid = m.group(1) if m else "?"
        raw = self._v.get(pid, QaVerdict(point_id=pid, clean=True, kind=IssueKind.JUDGE_OFFTOPIC))
        return raw


class TestJudgeDefense:
    def test_clean_dropped(self):
        v = QaVerdict(point_id="SP-01", clean=True, kind=IssueKind.JUDGE_OFFTOPIC)
        assert _validate_verdict(v, "SP-01") is None

    def test_kind_outside_whitelist_dropped(self):
        v = QaVerdict(point_id="SP-01", clean=False, kind=IssueKind.NUM_CONFLICT, reason="代码类别")
        assert _validate_verdict(v, "SP-01") is None  # Judge 不许发明代码判类别

    def test_empty_reason_dropped(self):
        v = QaVerdict(point_id="SP-01", clean=False, kind=IssueKind.JUDGE_STALE, reason="  ")
        assert _validate_verdict(v, "SP-01") is None

    def test_point_id_rebound(self):
        v = QaVerdict(point_id="OTHER", clean=False, kind=IssueKind.JUDGE_STALE, reason="旧甲方")
        got = _validate_verdict(v, "SP-01")
        assert got is not None and got.point_id == "SP-01"


class TestQaService:
    def test_offline_code_rules_only(self, settings):
        pts = [_pt("SP-01", star=True), _pt("SP-06")]
        ans = [_ans("SP-01", "已应答 [R1]", citations=[{"ref": "R1"}]),
               _ans("SP-06", "")]
        rep, _ = asyncio.run(QaService(settings).run(points=pts, answers=ans))
        # SP-01(★)已答、SP-06(普通)漏答 → 1 个 WARN，不触发 escalate
        assert rep.warn_count == 1 and rep.block_count == 0
        assert not rep.escalation_required

    def test_star_unanswered_escalates(self, settings):
        pts = [_pt("SP-01", star=True)]
        rep, _ = asyncio.run(QaService(settings).run(points=pts, answers=[_ans("SP-01", "")]))
        assert rep.block_count == 1
        assert rep.escalation_required
        assert rep.issues[0].kind == IssueKind.UNANSWERED_STAR

    def test_reconcile_checks_flow_into_report(self, settings):
        pts = [_pt("SP-01", "满足")]
        checks = [ParamCheck(req=_req("ST-1", "分辨率", "≥400万像素", star=True, value=4e6, unit="像素"),
                             verdict=Verdict.UNDER, reason="达不到")]
        rep, _ = asyncio.run(QaService(settings).run(points=pts,
                                                     answers=[_ans("SP-01", "应答 [R1]", citations=[{"ref": "R1"}])],
                                                     checks=checks))
        assert rep.block_count == 1
        assert any(i.kind == IssueKind.STAR_UNDER for i in rep.issues)

    def test_judge_stale_is_block(self, settings):
        fake = _FakeLLM({"SP-01": QaVerdict(point_id="SP-01", clean=False,
                                            kind=IssueKind.JUDGE_STALE,
                                            reason="应答仍带旧项目甲方", suggestion="替换为新甲方")})
        pts = [_pt("SP-01", "业绩要求")]
        ans = [_ans("SP-01", "我方曾服务旧甲方… [R1]", citations=[{"ref": "R1"}])]
        svc = QaService(settings, fake)
        rep, _ = asyncio.run(svc.run(points=pts, answers=ans))
        assert rep.block_count == 1
        assert rep.issues[0].kind == IssueKind.JUDGE_STALE
        assert fake.calls == 1

    def test_rewrite_loop_clears_fixable(self, settings):
        """打回改写成功 → 该点旧的 fixable issue 被剔除；不可修的保留。"""
        pts = [_pt("SP-02"), _pt("SP-09", star=True)]
        ans = [_ans("SP-02", ""), _ans("SP-09", "")]  # 都漏答：普通 WARN + ★ BLOCK

        async def rewrite(point_id, feedback: list[QaIssue]):
            return _ans(point_id, "重写后实质应答 [R1]", citations=[{"ref": "R1"}])

        svc = QaService(settings)  # 不带 llm → 只走代码判 + 改写
        rep, final = asyncio.run(svc.run(points=pts, answers=ans, rewrite=rewrite))
        # 普通漏答可自动补 → warn 清掉；★ 漏答也应被 rewrite 补上后清掉
        assert rep.block_count == 0
        assert rep.warn_count == 0
        assert len(final) == 2 and all(a.answer for a in final)

    def test_rewrite_unchanged_keeps_issue(self, settings):
        """改写器没真改（返回空/相同）→ issue 保留，不会无限循环。"""
        pts = [_pt("SP-02")]
        ans = [_ans("SP-02", "")]
        first = [0]

        async def rewrite(point_id, feedback):
            first[0] += 1
            return None  # 放弃改写

        rep, _ = asyncio.run(QaService(settings).run(points=pts, answers=ans, rewrite=rewrite))
        assert first[0] == 1  # 只尝试一次（max_attempts=1），没有死循环
        assert rep.warn_count == 1

    def test_needs_material_collected(self, settings):
        pts = [_pt("SP-01")]
        ans = [_ans("SP-01", "应答 [R1]", citations=[{"ref": "R1"}], missing=["报价一览表"])]
        rep, _ = asyncio.run(QaService(settings).run(points=pts, answers=ans))
        assert rep.needs_material == ["报价一览表"]


# ================================================================ 5. 新一轮审计回归（F1/F2/F4）


class TestNumericConflictsRegression:
    def test_inverted_warranty_phrasing_over_commit(self):
        """F1 回归：倒装 "我方承诺 60 个月质保服务"，数值前只有噪音标签"承诺"。
        旧逻辑 _topic_for 被噪音词短路 → 主题对不齐质保期能力池 → 超承诺漏检；
        现在须先认白名单主题（数值后窗口"质保"），再判 60月 > 硬能力 36月 → OVER_COMMIT。"""
        ans = [_ans("SP-05", "我方承诺 60 个月质保服务")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT

    def test_we_provide_years_warranty_over_commit(self):
        """F1 回归变体：无"质保期"字面、纯"提供 5 年质保服务"也要对齐到质保期。"""
        ans = [_ans("SP-05", "我司提供 5 年质保服务")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT

    def test_deadline_not_later_conflict(self):
        """F2 回归：150 天工期 与 "不晚于 120 天"（≤120）不相交 → 自相矛盾。
        （若 "不晚于" 不被映射成 ≤ 而退化成裸点 120，矛盾仍成立；关键是别误放）"""
        ans = [_ans("SP-04", "我方工期 150 日历天，交付工期不晚于 120 日历天")]
        issues = rules.numeric_conflicts(ans, [])
        assert any(i.kind == IssueKind.NUM_CONFLICT for i in issues)

    def test_deadline_late_vs_not_later_strict_boundary(self):
        """F2 回归：严格 "晚于 120"（>120）与 "不晚于 120"（≤120）在等值边界不相交 → 矛盾。
        若 "晚于/早于" 未被映射成严格 op，两个点 120 会在边界被误判共存 → 漏检。"""
        ans = [_ans("SP-04", "交付工期晚于 120 日历天即违约，交付工期不晚于 120 日历天")]
        issues = rules.numeric_conflicts(ans, [])
        assert any(i.kind == IssueKind.NUM_CONFLICT for i in issues)

    def test_strict_greater_and_upper_bound_conflict(self):
        """F4 回归：质保期>3年 与 ≤3年 等值边界不共存（>36 vs ≤36 无交集）→ NUM_CONFLICT。"""
        ans = [_ans("SP-05", "质保期大于 3 年，但整机质保期不超过 3 年")]
        issues = rules.numeric_conflicts(ans, [])
        assert any(i.kind == IssueKind.NUM_CONFLICT for i in issues)

    def test_inclusive_lower_and_upper_bound_coexist(self):
        """F4 回归：不少于3年(≥36) 与 不超过3年(≤36) 在 3 年整处相交 → 可共存，不误报。"""
        ans = [_ans("SP-05", "质保期不少于 3 年，质保期不超过 3 年")]
        assert rules.numeric_conflicts(ans, []) == []
