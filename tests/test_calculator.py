# -*- coding: utf-8 -*-
"""数值核对单元测试：解析陷阱 / 单位归一 / 比较方向 / 三态一灰 / ★负偏离记账。

全部纯代码、离线确定性；这是 Phase 4 的"计算器"逻辑单测（LLM 不参与比对）。
"""
from __future__ import annotations

from core.calculator.extract import canonical_topic, from_tender_doc, from_text
from core.calculator.numeric import compare_numeric, parse_numeric
from core.calculator.schemas import OfferClaim, NumericValue, ParamReq, Verdict
from core.calculator.service import Calculator
from core.parser.schemas import TenderDoc


# ---------- 解析陷阱：该抽的对、不该抽的一个都不抽 ----------
def test_parse_basic_and_multiplier() -> None:
    n = parse_numeric("分辨率≥400万像素")
    assert n is not None and n.value == 4_000_000 and n.unit == "像素" and n.operator == ">="


def test_parse_unit_normalization_year_and_month() -> None:
    assert parse_numeric("整体质保期须≥3年").value == 36  # 3 年 → 月
    assert parse_numeric("质保 36 个月").value == 36  # 同基准单位，可直接比
    assert parse_numeric("质保期≥5年").value == 60


def test_parse_storage_unit_to_mb() -> None:
    gb = parse_numeric("内存≥128GB").value
    tb = parse_numeric("有效存储容量≥120TB").value
    assert gb == 128 * 1024
    assert tb == 120 * 1024 * 1024  # 与 GB 同基准 → 可同量纲比较


def test_parse_calendar_day_is_day() -> None:
    n = parse_numeric("项目交付工期须≤120日历天", require_comparator=True)
    assert n is not None and n.value == 120 and n.unit == "天" and n.operator == "<="


def test_parse_upper_bound_using_nei_adjacent_only() -> None:
    # "2小时内" = 上界 ≤2 小时
    n = parse_numeric("重大故障须2小时内到达现场处置", require_comparator=True)
    assert n is not None and n.operator == "<=" and n.value == 2 and n.unit == "小时"
    # 裸"内"不能误伤"内存/内容"
    assert parse_numeric("内存 128GB DDR4 ECC。").operator is None


def test_noise_never_parsed_as_quantity() -> None:
    # 复合模式/标准号/分辨率乘积/付款百分比——都不得被当成达标数值
    for bad in ["并提供7×24小时响应", "符合GB/T28181协议", "（2560×1440）", "H.265", "合同签订后支付30%"]:
        assert parse_numeric(bad, require_comparator=True) is None, bad


def test_item_number_prefix_not_parsed_as_value() -> None:
    n = parse_numeric("★5.2 项目交付工期须≤120日历天（自合同签订次日起算）", require_comparator=True)
    assert n is not None and n.value == 120  # 5.2 是编号，不是数值


# ---------- 比较方向：等值边界 / 正负偏离 ----------
def test_compare_boundary_equal_is_conform_not_under() -> None:
    req = NumericValue(value=4_000_000, unit="像素", operator=">=", raw="≥400万像素")
    off = NumericValue(value=4_000_000, unit="像素", operator=None, raw="400万像素")
    v, _ = compare_numeric(req, off)
    assert v == Verdict.CONFORM.value  # 恰达边界 → 满足，不误报


def test_compare_under_over() -> None:
    req = NumericValue(value=4_000_000, unit="像素", operator=">=", raw="≥400万像素")
    assert compare_numeric(req, NumericValue(value=3_000_000, unit="像素", operator=None, raw="300万像素"))[0] == "under"
    assert compare_numeric(req, NumericValue(value=8_000_000, unit="像素", operator=None, raw="800万像素"))[0] == "over"
    # 反向约束：更低更优（照度）
    lim = NumericValue(value=0.005, unit="Lux", operator="<=", raw="≤0.005Lux")
    assert compare_numeric(lim, NumericValue(value=0.003, unit="Lux", operator=None, raw="0.003Lux"))[0] == "over"
    assert compare_numeric(lim, NumericValue(value=0.01, unit="Lux", operator=None, raw="0.01Lux"))[0] == "under"


def test_compare_offer_geq_treated_as_claimable_min() -> None:
    # 我方声明 "≥32路" → 最低可保证 32 路，对要求 ≥32 判满足而非 OVER
    req = NumericValue(value=32, unit="路", operator=">=", raw="≥32路")
    v, _ = compare_numeric(req, NumericValue(value=32, unit="路", operator=">=", raw="≥32路"))
    assert v == Verdict.CONFORM.value


# ---------- 抽取与配对：同义词 / 三态一灰 / ★负偏离记账 ----------
def test_canonical_topic_synonyms() -> None:
    assert canonical_topic("整机质保期") == "质保期"
    assert canonical_topic("有效存储容量") == "存储容量"
    assert canonical_topic("CPU核心数") == "CPU核心数"
    assert canonical_topic("交付工期") == "工期"


def _calc() -> Calculator:
    return Calculator()


def _req(rid: str, label: str, text: str, *, star: bool = False) -> ParamReq:
    n = parse_numeric(text, require_comparator=star)
    return ParamReq(
        id=rid, label=label, topic=canonical_topic(label),
        requirement=text, numeric=n, star=star, source="t",
    )


def _off(topic: str, raw: str, source: str = "s") -> OfferClaim:
    return OfferClaim(id=f"o-{topic}", label=topic, topic=topic, claim=raw,
                      numeric=parse_numeric(raw), source=source)


def test_star_under_flagged_in_summary() -> None:
    # ★ 分辨率要求 900 万，我方最强只有 800 万 → 应被记入 star_under（废标级）
    reqs = [_req("TP-1", "分辨率", "★ 分辨率≥900万像素", star=True)]
    offers = [_off("分辨率", "800 万像素（3840×2160）")]
    checks, summ = _calc().check(reqs, offers)
    assert checks[0].verdict == Verdict.UNDER
    assert summ.under == 1 and summ.star_under and "TP-1" in summ.star_under[0]
    assert checks[0].offer.source == "s"


def test_missing_offer_is_unknown_not_fabricated() -> None:
    # 招标要求"交付工期≤120天"，我方语料无数值承诺 → UNKNOWN(需人工)，绝不乱答
    reqs = [_req("ST-1", "工期", "交付工期须≤120日历天", star=True)]
    checks, summ = _calc().check(reqs, [])
    assert checks[0].verdict == Verdict.UNKNOWN and checks[0].needs_human
    assert summ.unknown == 1 and summ.needs_human


def test_pick_min_satisfying_offer_avoid_overclaim() -> None:
    # 同主题两型号：400万达标、800万也达标 → 应选刚过线(400万)而非标 800万(虚标更优)
    reqs = [_req("TP-1", "分辨率", "分辨率≥400万像素")]
    offers = [_off("分辨率", "400 万像素"), _off("分辨率", "800 万像素")]
    checks, _ = _calc().check(reqs, offers)
    assert checks[0].verdict == Verdict.CONFORM
    assert "400" in checks[0].offer.claim and "800" not in checks[0].offer.claim


def test_from_text_ignores_noise_labels() -> None:
    text = "- 某某能力：支持 ≥100 路分析。\n- 分辨率 400 万像素（2560×1440）。\n- 金额：860 万元。"
    claims = from_text(text, "t")
    topics = {c.topic for c in claims}
    assert topics == {"分辨率"}  # "支持…分析" 与 "金额(万元)" 都不是可自证达标的能力
