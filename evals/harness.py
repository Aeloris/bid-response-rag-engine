# -*- coding: utf-8 -*-
"""评测驱动：用 gold 评测集回放一条 pipeline，逐 case 收集指标。

定位：
- **只回放、不改引擎**：parse/ingest/retriever/generator/calculator/qa 全部原样调用，
  与 tests 里的 e2e 同一路径 —— 测的是"真引擎在带 gold 输入上的表现"，不是专门写的优等生。
- 确定性：mock 下同一输入 → 同一指标（可回归）；provider 切 dashscope 时同一代码出真模型版。
- provider 门控：needs_provider 的坏例（Judge 语义复审）只在非 mock 下执行，mock 下单列跳过，
  检出率分母只算已执行用例 —— 诚实，不给 mock 记一笔记不住的账。

返回普通 dict（可直接 json.dumps 成 eval_report.json；md 由 run.py 渲染）。
"""
from __future__ import annotations

import time
from pathlib import Path

from config.settings import Settings
from core.calculator import Calculator, extract
from core.embeddings import get_embedding_provider
from core.generator import Generator
from core.generator.query import build_query
from core.ingest import ingest_corpus
from core.parser.pipeline import parse_tender
from core.qa import QaService
from core.retriever import Retriever
from core.vector_store import VectorStore
from evals import metrics as m
from evals.adversarial import make_qa_scenarios
from evals.dataset import (
    PARSE_GOLD,
    evidence_key,
    gold_evidence_keys,
    point_gold_by_id,
)

def _load_offers(corpus_dir: Path, names: list[str]) -> list:
    """从语料文件抽取我方能力声明（source=文件名，与 extract.from_text 口径一致）。"""
    out: list = []
    for name in names:
        text = (corpus_dir / name).read_text(encoding="utf-8")
        out += extract.from_text(text, name)
    return out


def _real_mode(settings: Settings) -> bool:
    return settings.llm.provider == "dashscope"


async def run_harness(
    settings: Settings,
    llm,
    *,
    pdf_path: str | Path = "fixtures/tender_sample.pdf",
    corpus_dir: str | Path = "fixtures/corpus",
) -> dict:
    root = settings.repo_root
    pdf = Path(pdf_path) if Path(pdf_path).is_absolute() else root / pdf_path
    corpus = Path(corpus_dir) if Path(corpus_dir).is_absolute() else root / corpus_dir
    if not pdf.exists():
        raise FileNotFoundError(f"评测 fixture 不存在: {pdf}")
    if not corpus.exists():
        raise FileNotFoundError(f"评测语料目录不存在: {corpus}")
    k = settings.eval.top_k  # 检索指标口径：Recall@k / MRR@k（config eval.top_k，默认 5）

    timings: dict[str, float] = {}
    real = _real_mode(settings)

    # ---------------- 1. 解析（回放 Phase 1） ----------------
    t0 = time.perf_counter()
    doc, rep = await parse_tender(str(pdf), llm)
    timings["parse_sec"] = round(time.perf_counter() - t0, 3)

    gold_ids = [p.id for p in PARSE_GOLD.points]
    pred_ids = [p.id for p in doc.score_points]
    prec, rec, f1 = m.precision_recall(gold_ids, pred_ids)
    pred_by = {p.id: p for p in doc.score_points}
    exact = sum(
        1
        for g in PARSE_GOLD.points
        if (pred_by.get(g.id) is not None)
        and pred_by[g.id].score == g.score
        and pred_by[g.id].is_star == g.is_star
    )
    pred_star_rows = sorted(r.row_no for r in doc.tech_params if r.star)
    parse_result = {
        "points_gold": gold_ids,
        "points_predicted": pred_ids,
        "point_precision": round(prec, 4),
        "point_recall": round(rec, 4),
        "point_f1": round(f1, 4) if f1 is not None else None,
        "point_exactness": round(exact / len(PARSE_GOLD.points), 4),
        "star_clauses_predicted": len(doc.star_clauses),
        "star_clauses_gold": PARSE_GOLD.star_clauses,
        "star_clauses_ok": len(doc.star_clauses) == PARSE_GOLD.star_clauses,
        "tech_param_rows_predicted": len(doc.tech_params),
        "tech_param_rows_gold": PARSE_GOLD.tech_param_rows,
        "tech_param_rows_ok": len(doc.tech_params) == PARSE_GOLD.tech_param_rows,
        "tech_param_star_rows_predicted": pred_star_rows,
        "tech_param_star_rows_gold": sorted(PARSE_GOLD.tech_param_star_rows),
        "tech_param_star_rows_ok": pred_star_rows == sorted(PARSE_GOLD.tech_param_star_rows),
        "parse_report_ok": rep.ok,
    }

    # ---------------- 2. 入库 + 检索（回放 Phase 2） ----------------
    store = VectorStore(
        settings.vector_db.collection, settings.embedding.dimension, path=":memory:"
    )
    t0 = time.perf_counter()
    await ingest_corpus(str(corpus), store, settings)
    timings["ingest_sec"] = round(time.perf_counter() - t0, 3)
    retriever = Retriever(settings, store)
    embedding = get_embedding_provider(settings)
    gold_keys = gold_evidence_keys()

    def chunk_keys(chunks) -> list[str]:
        return [evidence_key(c.source, c.heading) for c in chunks]

    retrieval_rows: list[dict] = []
    for p in doc.score_points:
        query = build_query(p, doc.tender_title)
        # gold 没有该点映射（引擎多抽/新加评分点而 gold 未同步）→ 按"非可归因"单列，不 KeyError 崩整场评测
        gk = gold_keys.get(p.id) or []
        # 混合检索：引擎整条 Dense+BM25→RRF→Rerank，取前 k
        hyb = await retriever.retrieve(query, top_k=k)
        hyb_keys = chunk_keys(hyb)
        # 纯向量基线：只用 Dense 一路（与引擎同一 embedding），取前 k
        (qv,) = await embedding.embed([query])
        dense_payloads = store.search(qv, settings.retrieval.dense_top_k)[:k]
        den_keys = [evidence_key(d["source"], d["heading"]) for d in dense_payloads]

        row: dict = {"point_id": p.id, "gold_count": len(gk)}
        if gk:
            row["hybrid"] = {
                "recall_at_k": round(m.recall_at_k(hyb_keys, gk, k), 4),
                "mrr_at_k": round(m.mrr_at_k(hyb_keys, gk, k), 4),
            }
            row["dense"] = {
                "recall_at_k": round(m.recall_at_k(den_keys, gk, k), 4),
                "mrr_at_k": round(m.mrr_at_k(den_keys, gk, k), 4),
            }
        else:  # 非可归因点：不进分母，单列
            row["non_groundable"] = True
        retrieval_rows.append(row)

    groundable = [r for r in retrieval_rows if "non_groundable" not in r]
    non_groundable = [r["point_id"] for r in retrieval_rows if "non_groundable" in r]
    hy_r5 = m.safe_mean([r["hybrid"]["recall_at_k"] for r in groundable])
    hy_mrr = m.safe_mean([r["hybrid"]["mrr_at_k"] for r in groundable])
    den_r5 = m.safe_mean([r["dense"]["recall_at_k"] for r in groundable])
    den_mrr = m.safe_mean([r["dense"]["mrr_at_k"] for r in groundable])
    retrieval_result = {
        "k": k,
        "rows": retrieval_rows,
        "groundable_points": len(groundable),
        "non_groundable_points": non_groundable,
        "hybrid": {"mean_recall_at_k": hy_r5, "mean_mrr_at_k": hy_mrr},
        "dense": {"mean_recall_at_k": den_r5, "mean_mrr_at_k": den_mrr},
        "delta_hybrid_minus_dense": {
            "recall_at_k": round(hy_r5 - den_r5, 4) if hy_r5 is not None and den_r5 is not None else None,
            "mrr_at_k": round(hy_mrr - den_mrr, 4) if hy_mrr is not None and den_mrr is not None else None,
        },
    }

    # ---------------- 3. 应答生成（回放 Phase 3） ----------------
    t0 = time.perf_counter()
    answers, gsum = await Generator(settings, llm).generate(
        doc.score_points, retriever.retrieve, doc.tender_title
    )
    timings["generate_sec"] = round(time.perf_counter() - t0, 3)
    generation_result = {
        "answered_of_total": f"{gsum.answered}/{gsum.total}",
        "answered": gsum.answered,
        "total": gsum.total,
        "empty_context": gsum.empty_context,
        "needs_human": gsum.needs_human_count,
        "needs_material": gsum.needs_material,
    }

    # ---------------- 4. 数值核对（回放 Phase 4） ----------------
    offers = _load_offers(corpus, list(PARSE_GOLD.corpus))
    reqs = extract.from_tender_doc(doc)
    t0 = time.perf_counter()
    checks, csum = Calculator(settings).check(reqs, offers)
    timings["calc_sec"] = round(time.perf_counter() - t0, 3)
    calc_result = {
        "total": csum.total,
        "conform": csum.conform,
        "over": csum.over,
        "under": csum.under,
        "unknown": csum.unknown,
        "star_under": csum.star_under,
        "needs_human": csum.needs_human,
    }

    # ---------------- 5. QA：好例误报 + 坏例检出（回放 Phase 5） ----------------
    qa = QaService(settings, llm)
    t0 = time.perf_counter()
    qrep_good, _ = await qa.run(
        points=doc.score_points,
        answers=answers,
        checks=checks,
        offers=offers,
        tender_title=doc.tender_title,
        buyer=doc.buyer,
        deadline=doc.deadline,
    )
    timings["qa_good_sec"] = round(time.perf_counter() - t0, 3)
    good = {
        "id": "good_full_bid",
        "block_count": qrep_good.block_count,
        "warn_count": qrep_good.warn_count,
        "escalation_required": qrep_good.escalation_required,
        "escalation_expected": False,
        "false_positive_escalation": int(qrep_good.escalation_required),
        "kinds_found": sorted({i.kind.value for i in qrep_good.issues}),
    }

    # 此刻快照 = 整条合规流水线（解析+生成+合规QA）的 LLM 调用次数；
    # 下面的对抗坏例评测会再触发 Judge 复审（mock 也有 calls），不能并进"合规流水线"数字。
    compliance_calls = len(getattr(llm, "calls", []))

    bad_rows: list[dict] = []
    for scenario in make_qa_scenarios(answers, offers):
        spec = scenario.spec
        if spec.needs_provider and not real:
            bad_rows.append(
                {
                    "id": spec.id,
                    "label": spec.label,
                    "executed": False,
                    "skipped_reason": "Judge 语义复审需真模型（mock 恒 clean），未执行、不计入检出率",
                    "expected_kinds": [k.value for k in spec.expected_kinds],
                    "escalation_expected": spec.escalation_expected,
                }
            )
            continue
        # 该场景改了能力声明（weaken_offer）→ 重算数值核对；否则沿用合规 checks
        if scenario.offers:
            eff_offers = scenario.offers
            eff_checks, _ = Calculator(settings).check(reqs, eff_offers)
        else:
            eff_offers, eff_checks = offers, checks
        qrep_bad, _ = await qa.run(
            points=doc.score_points,
            answers=scenario.answers,
            checks=eff_checks,
            offers=eff_offers,
            tender_title=doc.tender_title,
            buyer=doc.buyer,
            deadline=doc.deadline,
        )
        found = {i.kind.value for i in qrep_bad.issues}
        expected = {k.value for k in spec.expected_kinds}
        detected = expected.issubset(found) and qrep_bad.escalation_required == spec.escalation_expected
        bad_rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "executed": True,
                "detected": bool(detected),
                "expected_kinds": sorted(expected),
                "kinds_found": sorted(found),
                "escalation_required": qrep_bad.escalation_required,
                "escalation_expected": spec.escalation_expected,
            }
        )

    executed_bad = [r for r in bad_rows if r.get("executed")]
    gated_bad = [r for r in bad_rows if not r.get("executed")]
    detected_count = sum(1 for r in executed_bad if r["detected"])
    qa_result = {
        "good_case": good,
        "good_fp_rate": m.fp_rate(good["false_positive_escalation"], 1),
        "bad_cases": bad_rows,
        "bad_detected_of_executed": f"{detected_count}/{len(executed_bad)}",
        "bad_detection_rate": m.detection_rate(detected_count, len(executed_bad)),
        "bad_gated_cases": [r["id"] for r in gated_bad],
    }

    # ---------------- 计时与调用量汇总 ----------------
    timings["total_sec"] = round(sum(timings.values()), 3)
    llm_calls = len(getattr(llm, "calls", []))  # mock 记录每次 chat；dashscope 无 calls → 0

    return {
        "meta": {
            "fixture": pdf.name,
            "corpus": list(PARSE_GOLD.corpus),
            "gold_version": PARSE_GOLD.meta.basis,
            "provider": settings.llm.provider,
            "real_mode": real,
            "embedding_provider": settings.embedding.provider,
            "rerank_provider": settings.rerank.provider,
            "top_k": k,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "parse": parse_result,
        "retrieval": retrieval_result,
        "generation": generation_result,
        "calc": calc_result,
        "qa": qa_result,
        "perf": {
            **timings,
            "compliance_llm_calls": compliance_calls,  # 整条合规流水线（解析+生成+合规QA）
            "llm_calls": llm_calls,  # 评测全流程（含对抗坏例重审）；兼容旧字段名
            "adversarial_llm_calls": llm_calls - compliance_calls,
        },
    }
