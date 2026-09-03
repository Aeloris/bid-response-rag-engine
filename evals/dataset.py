# -*- coding: utf-8 -*-
"""评测集 gold（带版本身份的人工标注）。

评测的第一原则（见 docs/eval.md 概念①）：**没有 gold 就没有指标，只有 demo**。
本文件就是"正确答案"的权威版本：谁来标、标哪天、依据哪份 fixture/语料都写在
AnnotationMeta 里 —— 标注集本身是可审计、可回放的仓库数据。

三类 gold：
- 解析 gold：fixtures/tender_sample.pdf 该解出哪些评分点 / ★条款数 / 技术参数星号行；
- 检索 gold：每个评分点该引用我方语料哪个块（source, heading）——**人工策展**，
  刻意不取"模型当前回链"（拿输出当 gold = 自己考自己，Recall 恒为 1 没意义）；
- 质检 gold：坏例该被拦到哪一类（期望 IssueKind / 期望 escalation）。

诚实口径：本评测集以"合成样例 fixture + 我方自备语料"为底座（仓库无真实客户标书，
也不伪造"脱敏真标书"）。指标结论只对"这一评测集 + 这一 provider 模式"成立，
报告中一律带 provider 与范围标注，绝不外推。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from core.qa.schemas import IssueKind

# ---------------------------------------------------------------------------
# gold 结构
# ---------------------------------------------------------------------------


class AnnotationMeta(BaseModel):
    annotated_by: str = Field(..., description="标注者（gold 由谁策展）")
    annotated_at: str = Field(..., description="标注日期")
    basis: str = Field(..., description="依据：fixture/语料/引擎版本的快照")


class EvalPointGold(BaseModel):
    """一个评分点的 gold：解析期望 + 检索证据 + 标注理由。"""

    id: str
    score: int | None = None
    is_star: bool = False
    evidence_type: list[str] = Field(default_factory=list)  # 标书要求附的材料
    gold_evidence: list[tuple[str, str]] = Field(
        default_factory=list, description="该点应引用的语料块 (source, heading)；空=非可归因点"
    )
    rationale: str = Field("", description="为什么这些块能支撑应答（面试可讲的人工策展依据）")


class ParseGold(BaseModel):
    """解析 gold：一份招标书 → 应当抽出的结构化内容。"""

    doc: str = Field(..., description="fixture PDF 相对路径")
    corpus: list[str] = Field(default_factory=list)
    points: list[EvalPointGold] = Field(default_factory=list)
    star_clauses: int = 0          # ★ 实质性条款条数
    tech_param_rows: int = 0       # 技术参数表行数
    tech_param_star_rows: list[int] = Field(default_factory=list)  # 关键参数(★)所在行
    meta: AnnotationMeta = Field(default_factory=lambda: AnnotationMeta.model_validate(
        {
            "annotated_by": "人工策展（Claude 辅助标注，依 fixture 正文与语料逐点核对）",
            "annotated_at": "2026-09-03",
            "basis": "fixtures/tender_sample.pdf（make_tender_fixture.py 生成）"
            " + fixtures/corpus/*.md @ commit b257016（Phase 7）",
        }
    ))


# ---------------------------------------------------------------------------
# 质检 gold：好例期望 + 坏例定义
# ---------------------------------------------------------------------------


class QaGoodCase(BaseModel):
    id: str
    note: str = ""
    escalation_expected: bool = False  # 合规草稿不应触发 BLOCK（不误伤）


class QaBadCase(BaseModel):
    """一个"坏例"的期望：被拦到哪些类 / 是否应 escalation / 需不需要真模型。

    needs_provider=True 的坏例（如张冠李戴）依赖 LLM-as-Judge 语义复审；
    mock 的 Judge fixture 恒 clean → 离线无法真测，run.py 将其单列并跳过计数，
    诚实标注为"待真模型"。
    """

    id: str
    label: str
    expected_kinds: list[IssueKind] = Field(default_factory=list)
    escalation_expected: bool = False
    needs_provider: bool = False
    scenario: str = Field("", description="坏例注入器名（adversarial.py 里同名函数）")
    note: str = ""


# ---------------------------------------------------------------------------
# 实例数据（单 fixture 版，见 docs/eval.md 评测集组成表）
# ---------------------------------------------------------------------------

SAMPLE_DOC = "fixtures/tender_sample.pdf"
SAMPLE_CORPUS = ["product-guide.md", "qualifications-and-service.md", "cases.md"]

PARSE_GOLD = ParseGold(
    doc=SAMPLE_DOC,
    corpus=SAMPLE_CORPUS,
    star_clauses=5,
    tech_param_rows=7,
    tech_param_star_rows=[1, 3, 5],
    points=[
        EvalPointGold(
            id="SP-01",
            score=10,
            evidence_type=["技术方案章节", "系统架构图"],
            gold_evidence=[
                ("product-guide.md", "智慧安防综合管理平台"),
                ("product-guide.md", "HC-AIServer 高性能分析主机"),
                ("cases.md", "高新区 A 区智慧安防改造项目（2025）"),
                ("cases.md", "科技城安防升级一期（2024）"),
            ],
            rationale="总体技术方案证据 = 平台层能力（电子地图/报警联动/GB28181 对接）"
            " + 算力支撑（AIServer）+ 同类园区落地案例（针对性）。具体硬件数值另由"
            " Phase4 数值核对把关，不进技术方案引用的检索 gold。",
        ),
        EvalPointGold(
            id="SP-02",
            score=15,
            evidence_type=["合同关键页", "验收证明"],
            gold_evidence=[
                ("cases.md", "高新区 A 区智慧安防改造项目（2025）"),  # 860万，已竣工验收
                ("cases.md", "科技城安防升级一期（2024）"),  # 520万，已终验
            ],
            rationale="招标要求同类园区安防集成业绩且单合同≥500万。高新区A区860万、"
            "科技城520万达标；中心商务区480万(<500万)且为楼宇方向 → 不作为达标业绩",
        ),
        EvalPointGold(
            id="SP-03",
            score=10,
            evidence_type=["人员简历", "社保证明"],
            gold_evidence=[
                ("qualifications-and-service.md", "关键人员"),
            ],
            rationale="项目经理王强（机电一级建造师+高级职称）出处唯一，见服务承诺语料"
            "「关键人员」。",
        ),
        EvalPointGold(
            id="SP-04",
            score=10,
            evidence_type=["实施计划方案"],
            gold_evidence=[
                ("qualifications-and-service.md", "商务条款接受度"),  # 最短实施周期 98 日历天
                ("cases.md", "科技城安防升级一期（2024）"),  # 利旧改造/无中断切换
                ("cases.md", "高新区 A 区智慧安防改造项目（2025）"),  # 竣工验收里程碑
            ],
            rationale="实施计划证据 = 工期承诺（98天）+ 利旧并行切换案例 + 验收里程碑案例。",
        ),
        EvalPointGold(
            id="SP-05",
            score=5,
            evidence_type=["售后服务承诺函"],
            gold_evidence=[
                ("qualifications-and-service.md", "售后与运维服务"),
            ],
            rationale="质保3年延保5年/7×24/2小时到场唯一出处：服务承诺语料「售后与运维服务」。",
        ),
        EvalPointGold(
            id="SP-06",
            score=20,
            evidence_type=["报价一览表"],
            gold_evidence=[],
            rationale="价格分=评标基准价÷报价×20，需我方实际报价，语料无报价能力 → "
            "非可归因点，不进检索 Recall/MRR 分母（单独列出）。",
        ),
    ],
)

# groundable = 有 gold 证据、进检索指标分母的评分点
GROUNDABLE_POINT_IDS = [p.id for p in PARSE_GOLD.points if p.gold_evidence]
NON_GROUNDABLE_POINT_IDS = [p.id for p in PARSE_GOLD.points if not p.gold_evidence]

QA_GOOD = QaGoodCase(
    id="good_full_bid",
    note="样例合规草稿（fixture 生成）→ 期望零 BLOCK、零误报",
    escalation_expected=False,
)

QA_BAD_CASES: list[QaBadCase] = [
    QaBadCase(
        id="bad_star_under",
        label="★关键参数负偏离（我方能力最强者不达标）",
        expected_kinds=[IssueKind.STAR_UNDER],
        escalation_expected=True,
        scenario="weaken_offer",
        note="把语料里「内存」最强能力 128GB 削弱为 64GB（模拟我方服务器内存不足），"
        "重新数值核对 → ★TP-3 内存 ≥128GB 应变 STAR_UNDER(BLOCK)。",
    ),
    QaBadCase(
        id="bad_drop_point",
        label="普通评分点漏答（SP-04 草稿缺失）",
        expected_kinds=[IssueKind.UNANSWERED_POINT],
        escalation_expected=False,
        scenario="drop_answer",
        note="从草稿摘掉 SP-04 应答 → 覆盖率判应报 UNANSWERED_POINT(WARN)。",
    ),
    QaBadCase(
        id="bad_overcommit",
        label="应答超承诺（交付工期 70 天 < 我方最短 98 天）",
        expected_kinds=[IssueKind.OVER_COMMIT],
        escalation_expected=False,
        scenario="overcommit_answer",
        note="把 SP-01 应答改为硬承诺「交付工期 70 日历天」，超出语料可支撑下限 98 天"
        " → 数值一致性判应报 OVER_COMMIT(WARN)。",
    ),
    QaBadCase(
        id="bad_stale_buyer",
        label="张冠李戴（应答错写采购人/旧项目）",
        expected_kinds=[IssueKind.JUDGE_STALE],
        escalation_expected=True,
        needs_provider=True,
        scenario="stale_answer",
        note="把 SP-01 应答内容换成指向错误采购人的旧项目描述 → 语义复审(Judge) 应报"
        " JUDGE_STALE(BLOCK)。依赖真模型：mock 的 Judge fixture 恒 clean，离线无法真测"
        " → run.py 单列、不计入 mock 检出率分母。",
    ),
]

# ---------------------------------------------------------------------------
# 便捷索引
# ---------------------------------------------------------------------------


def point_gold_by_id() -> dict[str, EvalPointGold]:
    return {p.id: p for p in PARSE_GOLD.points}


def evidence_key(source: str, heading: str) -> str:
    """把 (source, heading) 编成检索指标用的复合键（heading 不含此分隔符）。"""
    return f"{source} :: {heading}"


def gold_evidence_keys() -> dict[str, list[str]]:
    """每评分点 gold 证据的复合键 list（evidence_key），喂 metrics 纯函数。"""
    out: dict[str, list[str]] = {}
    for p in PARSE_GOLD.points:
        out[p.id] = [evidence_key(s, h) for s, h in p.gold_evidence]
    return out
