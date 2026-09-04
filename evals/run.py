# -*- coding: utf-8 -*-
"""eval-harness CLI：跑评测 → 落 data/eval/eval_report.{json,md} → 阈值门禁。

用法：
    uv run python -m evals.run                # 跑 mock 确定性基线并落报告
    uv run python -m evals.run --no-gate      # 只报告不设退出码（CI 或临时探索用）

报告口径（诚实声明，见 docs/eval.md）：
- provider=mock：确定性基线，可回归、可挂 CI；阈值门禁只在该模式下生效。
- 换真实模型：config.yaml llm.provider=dashscope（需已实现 DashScopeProvider 的 chat）；
  当前仓库 llm/dashscope_provider.py 仍是骨架 → 本命令会明确拒绝并提示，不半途装成功。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from config.settings import Settings, get_settings
from evals.harness import run_harness
from llm import get_provider


def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


# ---------------------------------------------------------------------------
# Markdown 报告渲染（人读；机器读走同目录 eval_report.json）
# ---------------------------------------------------------------------------


def render_markdown(r: dict, gate_rows: list[tuple[str, object, object, bool]] | None = None) -> str:
    m = r["meta"]
    lines: list[str] = []
    add = lines.append
    add(f"# 应标 Agent — Eval-Harness 评测报告")
    add("")
    add(f"- 评测对象：`{m['fixture']}` + 语料 `{', '.join(m['corpus'])}`")
    add(f"- provider：**{m['provider']}**（`real_mode={m['real_mode']}`） · embedding=`{m['embedding_provider']}` · rerank=`{m['rerank_provider']}`")
    add(f"- 检索口径：Recall@{m['top_k']} / MRR@{m['top_k']} · gold 版本：`{m['gold_version']}`")
    add(f"- 生成时间：{m['generated_at']}")
    add("")

    add("## 一、解析（评分点/★/参数表 vs gold）")
    p = r["parse"]
    add("")
    add("| 指标 | 值 |")
    add("|---|---|")
    add(f"| 评分点 精确率/召回率/F1 | {_fmt(p['point_precision'])} / {_fmt(p['point_recall'])} / {_fmt(p['point_f1'])} |")
    add(f"| 评分点(分值,★)逐点一致率 | {_fmt(p['point_exactness'])} |")
    add(f"| ★条款数 | 实测 {p['star_clauses_predicted']} = gold {p['star_clauses_gold']}：{'一致' if p['star_clauses_ok'] else '不一致'} |")
    add(f"| 技术参数行/★行 | 实测 {p['tech_param_rows_predicted']}·★{p['tech_param_star_rows_predicted']} = gold {p['tech_param_rows_gold']}·★{p['tech_param_star_rows_gold']}：{'一致' if p['tech_param_rows_ok'] and p['tech_param_star_rows_ok'] else '不一致'} |")
    add("")

    add("## 二、检索（混合 Dense+BM25→RRF vs 纯向量基线）")
    rt = r["retrieval"]
    add("")
    add("| 路 | Recall@{k} 均值 | MRR@{k} 均值 |".format(k=rt["k"]))
    add("|---|---|---|")
    add(f"| 混合检索 | {_fmt(rt['hybrid']['mean_recall_at_k'])} | {_fmt(rt['hybrid']['mean_mrr_at_k'])} |")
    add(f"| 纯向量(Dense) 基线 | {_fmt(rt['dense']['mean_recall_at_k'])} | {_fmt(rt['dense']['mean_mrr_at_k'])} |")
    d = rt["delta_hybrid_minus_dense"]
    add(f"| 差值(混合−基线) | {_fmt(d['recall_at_k'])} | {_fmt(d['mrr_at_k'])} |")
    add(f"- 可归因评分点 {rt['groundable_points']} 个；非可归因（无报价语料支撑，单列）：{', '.join(rt['non_groundable_points']) or '-'}")
    add("")
    add("逐点明细：")
    add("")
    add("| 点 | gold | 混合 R@k | 混合 MRR | Dense R@k | Dense MRR |")
    add("|---|---|---|---|---|---|")
    for row in rt["rows"]:
        if "non_groundable" in row:
            add(f"| {row['point_id']} | 0(非可归因) | - | - | - | - |")
        else:
            h, dn = row["hybrid"], row["dense"]
            add(f"| {row['point_id']} | {row['gold_count']} | {_fmt(h['recall_at_k'])} | {_fmt(h['mrr_at_k'])} | {_fmt(dn['recall_at_k'])} | {_fmt(dn['mrr_at_k'])} |")
    add("")

    add("## 三、应答生成 + 数值核对")
    g = r["generation"]
    c = r["calc"]
    add("")
    add(f"- 应答覆盖：{g['answered_of_total']}；空上下文/缺引用 {g['empty_context']}；需人工 {g['needs_human']}")
    add(f"- 待补材料：{'、'.join(g['needs_material']) if g['needs_material'] else '-'}")
    add(f"- 数值核对：{c['total']} 行 = conform {c['conform']} / over {c['over']} / under {c['under']} / unknown {c['unknown']}"
        + (f"；★负偏离 {c['star_under']}" if c["star_under"] else ""))
    add("")

    add("## 四、自检质检：好例误报 + 坏例检出")
    q = r["qa"]
    good = q["good_case"]
    add("")
    add(f"- 好例（合规草稿）：BLOCK {good['block_count']} / WARN {good['warn_count']} · 误报率 **{_fmt(q['good_fp_rate'])}**"
        + f" · 实测类别 {good['kinds_found']}")
    add(f"- 坏例检出：**{q['bad_detected_of_executed']}** → 检出率 **{_fmt(q['bad_detection_rate'])}**")
    add("")
    add("| 坏例 | 期望类别 | 是否执行 | 检出 | 实测类别 |")
    add("|---|---|---|---|---|")
    for b in q["bad_cases"]:
        if b.get("executed"):
            mark = "✅" if b["detected"] else "❌"
            add(f"| {b['label']} | {', '.join(b['expected_kinds'])} | ✅ | {mark} | {', '.join(b.get('kinds_found') or [])} |")
        else:
            add(f"| {b['label']} | {', '.join(b['expected_kinds'])} | 跳过 | - | {b.get('skipped_reason', '')} |")
    if q["bad_gated_cases"]:
        add("")
        add(f"> 门控坏例 {', '.join(q['bad_gated_cases'])} 需真模型（LLM-as-Judge）才能测 —— 当前 mock 下不计入检出率分母。")
    add("")

    add("## 五、性能（离线 mock，真实墙钟）")
    pf = r["perf"]
    add("")
    add(f"- 整条标端到端 **{_fmt(pf['total_sec'], 3)}s**（解析 {_fmt(pf['parse_sec'], 3)} / 入库 {_fmt(pf['ingest_sec'], 3)}"
        f" / 生成 {_fmt(pf['generate_sec'], 3)} / 数值核对 {_fmt(pf['calc_sec'], 3)} / QA {_fmt(pf['qa_good_sec'], 3)}）")
    add(f"- LLM 调用次数（整条合规流水线）：**{pf['compliance_llm_calls']}**"
        + (f"；评测全流程（含 {pf['adversarial_llm_calls']} 次对抗坏例重审）共 **{pf['llm_calls']}**" if pf.get("llm_calls") else ""))
    add("")

    if gate_rows is not None:
        add("## 六、阈值门禁（mock CI 基线）")
        add("")
        add("| 门禁项 | 实测 | 阈值 | 结果 |")
        add("|---|---|---|---|")
        for label, val, bar, passed in gate_rows:
            add(f"| {label} | {_fmt(val)} | {_fmt(bar)} | {'✅' if passed else '❌'} |")
        add("")

    add("---")
    add("> 诚实口径：以上数字只对「本评测集 + 当前 provider」成立，是确定性代码计数（可回归），"
        "不是对真实投标效果的宣称。真实模型版需实现 DashScope LLM/Embedding Provider 后，"
        "把 config.yaml `llm.provider` 切为 dashscope 再跑同一命令。简历引数须同时注明此口径。")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 阈值门禁
# ---------------------------------------------------------------------------


def gate_check(r: dict, th) -> list[tuple[str, object, object, bool]]:
    """按配置阈值比较，返回 (标签, 实测, 阈值, 是否通过)。"""
    p, rt, q = r["parse"], r["retrieval"], r["qa"]
    candidates: list[tuple[str, object, float, str]] = [
        ("解析评分点 F1", p["point_f1"], th.parse_point_f1, "ge"),
        ("混合检索 Recall@k", rt["hybrid"]["mean_recall_at_k"], th.retrieval_hybrid_recall_at_k, "ge"),
        ("混合检索 MRR@k", rt["hybrid"]["mean_mrr_at_k"], th.retrieval_hybrid_mrr_at_k, "ge"),
        ("坏例检出率", q["bad_detection_rate"], th.qa_bad_detection_rate, "ge"),
        ("好例误报率", q["good_fp_rate"], th.qa_good_fp_rate_max, "le"),
    ]
    rows: list[tuple[str, object, object, bool]] = []
    for label, val, bar, cmp_op in candidates:
        if not isinstance(val, (int, float)):
            rows.append((label, None, bar, False))
            continue
        passed = val >= bar if cmp_op == "ge" else val <= bar
        rows.append((label, round(float(val), 4), bar, passed))
    return rows


def main() -> int:
    # Windows 控制台默认 GBK：✅/❌ 等符号打印会 UnicodeEncodeError 崩溃。
    # 强制 stdout/stderr 走 UTF-8（errors=replace 降级为 '?' 而不是炸），保证 CLI 永不因控制台编码退出。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # 无 reconfigure 的流（如已关闭/测试替身）直接跳过
            pass

    ap = argparse.ArgumentParser(description="应标 Agent eval-harness")
    ap.add_argument("--no-gate", action="store_true", help="只报告，不做阈值门禁退出")
    args = ap.parse_args()

    settings: Settings = get_settings()
    if settings.llm.provider == "dashscope":
        print(
            "✗ llm.provider=dashscope 但 DashScope LLM Provider 尚未实现（llm/dashscope_provider.py 为骨架，"
            "chat 会 raise NotImplementedError）。\n"
            "  真实模型版评测需先实现该 Provider；当前请保持 mock 跑确定性基线。",
            file=sys.stderr,
        )
        return 2

    out_dir = settings.repo_root / settings.eval.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run_harness(settings, get_provider(settings)))

    gate_rows = None if args.no_gate else gate_check(report, settings.eval.thresholds)
    (out_dir / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "eval_report.md").write_text(
        render_markdown(report, gate_rows), encoding="utf-8"
    )
    print(f"[ok] eval_report.json / .md -> {out_dir}")

    # 终端速览
    q = report["qa"]
    print(f"  parse F1 {_fmt(report['parse']['point_f1'])}"
          f" | retrieval R@{report['retrieval']['k']} {_fmt(report['retrieval']['hybrid']['mean_recall_at_k'])}"
          f" MRR {_fmt(report['retrieval']['hybrid']['mean_mrr_at_k'])}")
    print(f"  qa 好例误报 {_fmt(q['good_fp_rate'])} | 坏例检出 {q['bad_detected_of_executed']}"
          f" | 端到端 {report['perf']['total_sec']}s")

    if gate_rows is None:
        print("[ok] --no-gate：仅报告，跳过门禁")
        return 0
    failed = [row for row in gate_rows if not row[3]]
    for label, val, bar, passed in gate_rows:
        print(f"  gate {'✅' if passed else '❌'} {label}: 实测 {_fmt(val)} vs 阈值 {_fmt(bar)}")
    if failed:
        print(f"[gate] {len(failed)} 项低于阈值 → exit 1（mock 确定性基线，不可静默回退）", file=sys.stderr)
        return 1
    print("[gate] 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
