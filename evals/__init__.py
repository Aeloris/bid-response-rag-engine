# -*- coding: utf-8 -*-
"""Phase 8 评测（eval-harness）：带 gold 的评测集 + 指标 + 确定性回归报告。

包结构：
- dataset.py      评测集 gold（人工策展、版本化）：解析/检索/质检三类"正确答案"
- adversarial.py  坏例注入器（纯函数）：把"好草稿"改出可标定的坏
- metrics.py      指标纯函数（解析 P/R/F1、Recall@k、MRR、检出率、误报率）
- harness.py      评测驱动：回放 pipeline → 逐 case 收集
- run.py          CLI：产 data/eval_report.json + .md，阈值门禁（低于则 exit 1）
"""
