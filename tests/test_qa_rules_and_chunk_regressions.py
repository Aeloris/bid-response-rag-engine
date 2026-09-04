# -*- coding: utf-8 -*-
"""第四轮终审回归（P1 core）：QA 数值扫描 + chunker 死循环门禁。

修复对象（git log / README 变更记录）：
1. `_iter_claims` 遇到句内首个数字不可比（价格 500万元 / 型号 / 条号…）即 break → 把同一
   逗号小句里后面的真实承诺一起放弃（"投标总价500万元并承诺整机质保期4年"漏报 4年 超承诺）。
   现改为跳过该数字继续扫（parse_numeric 只认窗口开头首个数字，不代表后段没有可比量）。
2. `_topic_after` 前向窗口跨过并列词把 150TB 归给 "及" 之后的 质保期（错挂主题）。现窗口在
   首个分界字（及/与/和/并且/或/标点…）处截断，只认本数量短语内的主题词。
3. `ChunkingConfig.overlap_chars >= max_chars` 会让 `_split_long` 切不出前进 → 死循环挂死入库；
   启动即抛 ValueError（fail fast）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import ChunkingConfig
from core.calculator.schemas import NumericValue, OfferClaim
from core.generator.schemas import PointAnswer
from core.qa import rules
from core.qa.schemas import IssueKind


def _ans(point_id: str, text: str) -> PointAnswer:
    return PointAnswer(point_id=point_id, answer=text)


def _off(topic: str, value: float, unit: str, claim: str = "能力") -> OfferClaim:
    return OfferClaim(id="o", label=topic, topic=topic, claim=claim,
                      numeric=NumericValue(value=value, unit=unit))


# ================================================================ 1. run-on 首个数字不可比
class TestRunOnLeadingNonComparable:
    def test_price_prefix_does_not_suppress_later_overcommit(self):
        """"…投标总价500万元并承诺整机质保期4年"：500万 不是可比量，但不能把后面的 4年 放弃。"""
        ans = [_ans("SP-05", "投标总价500万元并承诺整机质保期4年")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]  # 我方上限 36 月
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT

    def test_unitless_model_no_first_still_scans_later_commit(self):
        """型号/编号等无单位前缀同样只跳过，不吞后面真承诺。"""
        ans = [_ans("SP-05", "本型号D500及整机质保期5年")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]
        issues = rules.numeric_conflicts(ans, offers)
        assert issues and issues[0].kind == IssueKind.OVER_COMMIT

    def test_within_cap_runon_stays_clean(self):
        """加修复后扫描更多，但没超上限就不能新增误报。"""
        ans = [_ans("SP-05", "投标总价500万元并承诺整机质保期3年")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]
        assert rules.numeric_conflicts(ans, offers) == []


# ================================================================ 2. 前向窗口跨并列词偷主题
class TestTopicAfterConjunctionGuard:
    def test_does_not_steal_topic_across_conjunction(self):
        from core.qa.rules import _topic_after

        base = "提供扩展存储150TB及整机质保期3年"
        assert _topic_after(base, base.index("150")) == ""  # 不该被归给 "及" 后的 质保期

    def test_inverted_adjacent_topic_still_found(self):
        from core.qa.rules import _topic_after

        base = "提供扩展3年质保服务"  # 倒装：数值+单位+主题 紧邻，无并列词
        assert _topic_after(base, base.index("3"))  # 非空 = 还能抓到主题（正例不被误伤）

    def test_end_to_end_capacity_miss_not_misattributed(self):
        """错挂被防住后：150TB 不产生指向质保期的假冲突/假超承诺（宁漏不误导）。"""
        ans = [_ans("SP-05", "提供扩展存储150TB及整机质保期3年")]
        offers = [_off("质保期", 36, "月", "整机质保3年")]
        kinds = {i.kind for i in rules.numeric_conflicts(ans, offers)}
        assert IssueKind.OVER_COMMIT not in kinds and IssueKind.NUM_CONFLICT not in kinds


# ================================================================ 3. chunker 配置死循环门禁
class TestChunkOverlapGuard:
    def test_overlap_below_max_is_valid(self):
        cfg = ChunkingConfig(max_chars=1200, overlap_chars=100)
        assert cfg.overlap_chars < cfg.max_chars

    def test_overlap_ge_max_rejected_at_config(self):
        """overlap>=max 会让 _split_long 死循环 → 配置阶段就必须拒绝。"""
        with pytest.raises(ValidationError):
            ChunkingConfig(max_chars=1200, overlap_chars=1200)
        with pytest.raises(ValidationError):
            ChunkingConfig(max_chars=800, overlap_chars=1000)
