# -*- coding: utf-8 -*-
"""坏例注入器（纯函数）：把"合规草稿"改出可标定的坏，供质检检出率评测。

坏例 = 人为注入的缺陷草稿，gold（期望类别）来自注入语义本身，**绝不**来自"看 mock
输出反推"（那等于用被测对象给自己打分）。四个坏例对应三类真实投标事故：

- 我方能力不达标却照答      → weaken_offer  （★负偏离 = 废标级）
- 草稿漏掉一个评分点        → drop_answer
- 应答硬承诺超出我方能力    → overcommit_answer（开标拿不出支撑即失信）
- 错把旧项目/别家内容贴过来 → stale_answer  （张冠李戴，需语义复审）

每个坏例都返回 (变体草稿, 变体能力声明)，harness 用它们重跑数值核对 + QA。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.calculator.schemas import OfferClaim
from core.generator.schemas import Citation, PointAnswer
from evals.dataset import QA_BAD_CASES, QaBadCase

# ---------------------------------------------------------------------------
# 坏例对象：spec（期望）+ 变体草稿 + 变体能力
# ---------------------------------------------------------------------------


@dataclass
class QaScenario:
    spec: QaBadCase
    answers: list[PointAnswer]
    offers: list[OfferClaim] | None = None  # None=能力声明不变（沿用合规 offers/checks）

    @property
    def id(self) -> str:
        return self.spec.id


def _copy_offers(offers: list[OfferClaim]) -> list[OfferClaim]:
    return [o.model_copy(deep=True) for o in offers]


def _point_answer(point_id: str, answer: str, *, source: str, heading: str) -> PointAnswer:
    """构造一条带合法引用骨架的应答（ref 编号仅供覆盖/Judge 通道，不做真实性承诺）。"""
    return PointAnswer(
        point_id=point_id,
        answer=answer,
        citations=[
            Citation(
                ref="R1",
                chunk_id="0000000000000000",  # 占位；真实链路会由生成器回填索引元数据
                source=source,
                heading=heading,
            )
        ],
        covered_evidence=[],
        missing_evidence=[],
        needs_human=False,
        note="",
    )


# ---------------------------------------------------------------------------
# 注入器（每个返回「变体 answers, 变体 offers」，原对象不被修改）
# ---------------------------------------------------------------------------


def weaken_offer(answers, offers: list[OfferClaim]):
    """★负偏离：把我方「内存」最强能力从 128GB 削弱到 64GB（草稿不变）。

    数值核对会因 64GB < 招标 ★≥128GB 判 UNDER，且该参数行是★（TP-3）→ 复核应为
    STAR_UNDER(BLOCK)。模拟"我方真就只够 64GB 却照常投标"的最坏能力场景。
    """
    out = _copy_offers(offers)
    for o in out:
        # parse_numeric 会把 128GB 归一成 131072.0 MB（基准单位），所以不能按 unit=="GB" 过滤，
        # 改按"主题内存 + 声明含 128GB"定位，把能力削到 64GB（=65536 MB）。
        if o.topic == "内存" and o.numeric is not None and "128GB" in o.claim:
            o.numeric.value = 64.0 * 1024  # 65536 MB
            o.numeric.raw = o.numeric.raw.replace("128GB", "64GB")
            o.claim = o.claim.replace("128GB", "64GB")
    return answers, out


def drop_answer(answers: list[PointAnswer], offers=None, point_id: str = "SP-04"):
    """漏答：从草稿里摘掉一个普通评分点（模拟生成环节遗漏）。"""
    kept = [a for a in answers if a.point_id != point_id]
    return kept, None


def overcommit_answer(answers: list[PointAnswer], offers=None, point_id: str = "SP-01"):
    """超承诺：把某点评应答改成硬承诺"交付工期 70 日历天"。

    我方语料最短实施周期为 98 日历天（商务条款接受度），承诺 70 < 98 →
    数值一致性判应 OVER_COMMIT（对投标 Agent：拿不出支撑即失信）。
    """
    repl = _point_answer(
        point_id,
        "我方承诺交付工期 70 日历天完成全部实施、系统联调与竣工验收通过 [R1]。",
        source="qualifications-and-service.md",
        heading="商务条款接受度",
    )
    return [repl if a.point_id == point_id else a for a in answers], None


def stale_answer(answers: list[PointAnswer], offers=None, point_id: str = "SP-01"):
    """张冠李戴：把某点评应答换成"看着专业、对象却错"的旧项目内容。

    内容指向与本次招标无关的采购人/项目（语义错位，代码判不出 → 需 LLM-as-Judge
    复审 buyer/title 是否对得上 → JUDGE_STALE）。
    """
    repl = _point_answer(
        point_id,
        "我方针对 XX市大数据管理局 政务数据治理项目已部署同类视频分析平台，项目金额 1200 万元，"
        "于 2025 年底通过验收，可直接复用该项目的算法与平台集成经验 [R1]。",
        source="product-guide.md",
        heading="智慧安防综合管理平台",
    )
    return [repl if a.point_id == point_id else a for a in answers], None


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

_SCENARIO_BUILDERS: dict[str, object] = {
    "weaken_offer": weaken_offer,
    "drop_answer": drop_answer,
    "overcommit_answer": overcommit_answer,
    "stale_answer": stale_answer,
}


def make_qa_scenarios(answers: list[PointAnswer], offers: list[OfferClaim]) -> list[QaScenario]:
    """按 dataset.QA_BAD_CASES 组装全部坏例场景（含 needs_provider 的 Judge 场景）。

    是否执行由 harness/run 依 provider 决定（mock 下跳过 Judge 场景并单列）。
    """
    by_id: dict[str, QaBadCase] = {c.id: c for c in QA_BAD_CASES}
    scenarios: list[QaScenario] = []
    for spec in QA_BAD_CASES:
        builder = _SCENARIO_BUILDERS[spec.scenario]
        var_answers, var_offers = builder(answers, offers)  # type: ignore[operator]
        scenarios.append(QaScenario(spec=by_id[spec.id], answers=var_answers, offers=var_offers))
    return scenarios
