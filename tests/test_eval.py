# -*- coding: utf-8 -*-
"""Phase 8 eval-harness 测试：指标纯函数 / gold 数据 / 坏例注入 / harness 报告。

诚实口径（与 docs/eval.md 一致）：
- 全程 mock、离线确定性：同一输入应产出同一指标，这里把"全绿基线"锁进测试；
- 坏例里"张冠李戴"依赖 LLM-as-Judge，mock 恒 clean → 必须被门控跳过、不计入检出率
  （断言 gated 而非"期待它检出"）—— 不给 mock 记一笔记不住的账。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from config.settings import get_settings
from core.calculator import extract
from core.generator.schemas import PointAnswer
from core.ingest import ingest_corpus
from core.qa.schemas import IssueKind
from core.vector_store import VectorStore
from evals import metrics as m
from evals.adversarial import (
    drop_answer,
    make_qa_scenarios,
    overcommit_answer,
    stale_answer,
    weaken_offer,
)
from evals.dataset import (
    PARSE_GOLD,
    QA_BAD_CASES,
    GROUNDABLE_POINT_IDS,
    evidence_key,
    gold_evidence_keys,
    point_gold_by_id,
)
from evals.harness import run_harness
from evals.run import gate_check, render_markdown
from llm.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "fixtures" / "corpus"


# ===========================================================================
# 1) 指标纯函数（手算小例）
# ===========================================================================


def test_metrics_precision_recall_f1():
    p, r, f1 = m.precision_recall(gold=["a", "b", "c"], pred=["a", "b", "d"])
    assert p == pytest.approx(2 / 3)
    assert r == pytest.approx(2 / 3)
    assert f1 == pytest.approx(2 / 3)
    # 完全重合
    p, r, f1 = m.precision_recall(gold=["a"], pred=["a"])
    assert (p, r, f1) == (1.0, 1.0, 1.0)
    # 无交集 → F1 None（p+r=0）
    p, r, f1 = m.precision_recall(gold=["x"], pred=["y"])
    assert (p, r) == (0.0, 0.0)
    assert f1 is None
    # 空集合约定（无 gold 且无 pred → 视为满分）
    p, r, _ = m.precision_recall(gold=[], pred=[])
    assert (p, r) == (1.0, 1.0)


def test_metrics_recall_and_mrr_at_k():
    gold = ["g1", "g2", "g3"]
    ranked = ["n1", "g1", "n2", "g3", "g2"]  # top-5 内 3 条全中，首中在 rank2
    assert m.recall_at_k(ranked, gold, 5) == pytest.approx(1.0)
    assert m.mrr_at_k(ranked, gold, 5) == pytest.approx(0.5)
    # 只有前 k 才算
    assert m.recall_at_k(ranked, gold, 1) == pytest.approx(0.0)
    assert m.recall_at_k(["g2", "n"], gold, 2) == pytest.approx(1 / 3)
    assert m.mrr_at_k(["n1", "n2", "g2"], gold, 5) == pytest.approx(1 / 3)
    assert m.mrr_at_k(["n1"], gold, 5) == 0.0


def test_recall_at_k_deduplicates_duplicate_hits():
    """F8 回归：同一 gold 键在 top-k 里重复命中（如同一出处被切出多个块）只算一次，
    否则 Recall@k 会虚高超过 1.0。"""
    # 旧实现 hit=2/1=2.0；正确去重后 = 1.0
    assert m.recall_at_k(["g1", "g1"], ["g1"], 2) == pytest.approx(1.0)
    # top-k 里 gold 只命中一种、但出现两次 → 1/2 而非 2/2
    assert m.recall_at_k(["g1", "g1", "n"], ["g1", "g2"], 3) == pytest.approx(0.5)


def test_metrics_detection_and_fp_rate():
    assert m.detection_rate(3, 3) == 1.0
    assert m.detection_rate(2, 3) == pytest.approx(2 / 3)
    assert m.detection_rate(0, 0) == 0.0  # 无可执行用例 → 0（诚实不给分母记账）
    assert m.fp_rate(0, 1) == 0.0
    assert m.fp_rate(1, 1) == 1.0


def test_metrics_safe_mean():
    assert m.safe_mean([0.5, 0.75]) == pytest.approx(0.625)
    assert m.safe_mean([]) is None


# ===========================================================================
# 2) gold 数据层
# ===========================================================================


def test_dataset_gold_shape():
    assert [p.id for p in PARSE_GOLD.points] == ["SP-01", "SP-02", "SP-03", "SP-04", "SP-05", "SP-06"]
    assert PARSE_GOLD.star_clauses == 5
    assert PARSE_GOLD.tech_param_rows == 7
    assert PARSE_GOLD.tech_param_star_rows == [1, 3, 5]
    assert GROUNDABLE_POINT_IDS == ["SP-01", "SP-02", "SP-03", "SP-04", "SP-05"]
    # 非可归因点（价格分，无报价语料）有 gold 结构但无证据
    assert point_gold_by_id()["SP-06"].gold_evidence == []
    # gold 有版本身份（面试谈资：标注集是可审计、可回放的仓库数据）
    assert PARSE_GOLD.meta.annotated_by and PARSE_GOLD.meta.annotated_at and PARSE_GOLD.meta.basis


def test_qa_bad_cases_expectations():
    by_id = {c.id: c for c in QA_BAD_CASES}
    assert by_id["bad_star_under"].expected_kinds == [IssueKind.STAR_UNDER]
    assert by_id["bad_star_under"].escalation_expected is True
    assert by_id["bad_drop_point"].expected_kinds == [IssueKind.UNANSWERED_POINT]
    assert by_id["bad_overcommit"].expected_kinds == [IssueKind.OVER_COMMIT]
    # 只有张冠李戴依赖语义复审（真模型 Judge）
    assert by_id["bad_stale_buyer"].needs_provider is True
    assert by_id["bad_stale_buyer"].expected_kinds == [IssueKind.JUDGE_STALE]
    assert all(not c.needs_provider for c in QA_BAD_CASES if c.id != "bad_stale_buyer")


def test_gold_evidence_all_exist_in_chunk_index(settings):
    """gold 的 (source, heading) 必须真实存在于切块索引 —— 防标不存在的块当正确答案。"""
    store = VectorStore(settings.vector_db.collection, settings.embedding.dimension, path=":memory:")
    asyncio.run(ingest_corpus(str(CORPUS), store, settings))
    real = {evidence_key(p["source"], p["heading"]) for p in store.all_payloads()}
    for pid, keys in gold_evidence_keys().items():
        if not keys:
            continue
        missing = [k for k in keys if k not in real]
        assert not missing, f"{pid} gold 证据不在语料索引: {missing}"


# ===========================================================================
# 3) 坏例注入器（对真抽取的能力/草稿做变异）
# ===========================================================================


def _offers():
    """从语料真抽我方能力声明（与 harness._load_offers 同路径）。"""
    out = []
    for f in PARSE_GOLD.corpus:
        text = (CORPUS / f).read_text(encoding="utf-8")
        out += extract.from_text(text, f)
    return out


def _fake_answers(n: int = 6) -> list[PointAnswer]:
    """6 条只带 point_id 的空草稿（覆盖 SP-01..SP-06），够注入器判断用。"""
    return [PointAnswer(point_id=f"SP-{i:02d}") for i in range(1, n + 1)]


def test_weaken_offer_cuts_memory_capability():
    offers = _offers()
    assert any(o.topic == "内存" and o.numeric and "128GB" in o.claim for o in offers)
    _, weak = weaken_offer([], offers)
    mem = [o for o in weak if o.topic == "内存" and o.numeric]
    assert len(mem) == 1
    assert mem[0].numeric.value == pytest.approx(64 * 1024)  # 64GB → 归一单位 MB
    # 原 offers 未被改（纯函数不污染调用方）
    assert any(o.topic == "内存" and o.numeric and o.numeric.value == pytest.approx(128 * 1024) for o in offers)


def test_drop_answer_removes_point():
    answers = _fake_answers()
    kept, _ = drop_answer(answers, "SP-04")
    assert len(kept) == 5
    assert "SP-04" not in {a.point_id for a in kept}


def test_overcommit_and_stale_replace_point():
    answers = _fake_answers()
    repl, _ = overcommit_answer(answers, "SP-01")
    assert repl[0].point_id == "SP-01" and "70 日历天" in repl[0].answer
    srepl, _ = stale_answer(answers, "SP-01")
    assert srepl[0].point_id == "SP-01" and "大数据管理局" in srepl[0].answer


def test_make_qa_scenarios_matches_specs():
    answers, offers = _fake_answers(), _offers()
    scenarios = make_qa_scenarios(answers, offers)
    assert [s.id for s in scenarios] == [c.id for c in QA_BAD_CASES]
    for s in scenarios:
        assert s.spec.id == s.id
    # 仅 weaken_offer 变异能力声明（其余沿用合规 offers/checks）
    assert scenarios[0].offers is not None
    assert all(sc.offers is None for sc in scenarios[1:])


# ===========================================================================
# 4) harness 全链路报告（module 级跑一次，共享给多个断言）
# ===========================================================================


@pytest.fixture(scope="module")
def eval_report():
    settings = get_settings()  # 不依赖 function 级 settings 夹具 → 可 module 级共享
    return asyncio.run(run_harness(settings, MockProvider(settings)))


def test_harness_parse_metrics(eval_report):
    p = eval_report["parse"]
    assert p["point_f1"] == 1.0
    assert p["point_exactness"] == 1.0
    assert p["star_clauses_ok"] and p["tech_param_rows_ok"] and p["tech_param_star_rows_ok"]
    # 评分点集合一致（顺序敏感比较会脆，按 id 集判）
    assert sorted(p["points_predicted"]) == sorted(p["points_gold"])


def test_harness_retrieval_metrics(eval_report):
    rt = eval_report["retrieval"]
    assert rt["k"] == 5
    assert rt["groundable_points"] == 5
    assert sorted(rt["non_groundable_points"]) == ["SP-06"]
    assert len(rt["rows"]) == 6
    hy = rt["hybrid"]
    assert hy["mean_recall_at_k"] is not None and hy["mean_mrr_at_k"] is not None
    assert 0 < hy["mean_recall_at_k"] <= 1.0 and 0 <= hy["mean_mrr_at_k"] <= 1.0


def test_harness_qa_good_and_bad(eval_report):
    q = eval_report["qa"]
    assert q["good_fp_rate"] == 0.0  # 合规草稿不误拦
    assert q["bad_detected_of_executed"] == "3/3"
    assert q["bad_detection_rate"] == 1.0
    assert sorted(q["bad_gated_cases"]) == ["bad_stale_buyer"]  # Judge 场景诚实门控
    executed = [b for b in q["bad_cases"] if b.get("executed")]
    assert len(executed) == 3 and all(b["detected"] for b in executed)
    kinds = {k for b in executed for k in b["kinds_found"]}
    assert {"star_under", "unanswered_point", "over_commit"} <= kinds


def test_harness_meta_and_perf(eval_report):
    assert eval_report["meta"]["provider"] == "mock"
    assert eval_report["meta"]["real_mode"] is False
    assert eval_report["meta"]["top_k"] == 5
    assert eval_report["perf"]["total_sec"] > 0
    assert eval_report["perf"]["llm_calls"] >= 1
    assert eval_report["generation"]["answered_of_total"] == "5/6"
    assert eval_report["calc"]["under"] == 0
    # F10：合规流水线单独计数，对抗坏例重审另列；总和一致
    pf = eval_report["perf"]
    executed = [b for b in eval_report["qa"]["bad_cases"] if b.get("executed")]
    assert pf["compliance_llm_calls"] >= 1
    assert pf["llm_calls"] == pf["compliance_llm_calls"] + pf["adversarial_llm_calls"]
    assert pf["adversarial_llm_calls"] >= len(executed)  # 重审次数 ≥ 已执行坏例数


# ===========================================================================
# 5) 阈值门禁 + markdown 渲染
# ===========================================================================


def test_gate_all_pass_on_mock_baseline(eval_report, settings):
    rows = gate_check(eval_report, settings.eval.thresholds)
    assert len(rows) == 5
    assert all(passed for _, _, _, passed in rows), rows


def test_gate_flags_regression(eval_report, settings):
    bad = dict(eval_report)
    parse = dict(eval_report["parse"])
    parse["point_f1"] = 0.1  # 模拟解析回退
    bad["parse"] = parse
    rows = gate_check(bad, settings.eval.thresholds)
    fail = [label for label, _, _, passed in rows if not passed]
    assert "解析评分点 F1" in fail


def test_render_markdown_contains_sections(eval_report, settings):
    rows = gate_check(eval_report, settings.eval.thresholds)
    md = render_markdown(eval_report, rows)
    assert "Eval-Harness 评测报告" in md
    assert "## 六、阈值门禁" in md
    assert "坏例检出" in md
    assert "bad_stale_buyer" in md  # 门控坏例如实单列
    assert "诚实口径" in md
    assert md.count("|") > 10
