# -*- coding: utf-8 -*-
"""指标纯函数（Phase 8）。

口径哲学（呼应全项目"能算的绝不让模型算"）：
- 全部指标是**确定性代码计数**，与引擎/LLM 无关 —— mock 也能算出可回归的值；
- 本文件不做 I/O、不 import 引擎，只吃 list/set → 单测即手算小例断言；
- 检索指标约定：gold 非空才有效（评测集把"无可归因证据"的评分点单列，不进分母）。

常用指标：
- 解析：精确率/召回率/F1（评分点集合）；
- 检索：Recall@k（gold 证据进没进 top-k）、MRR@k（第一条相关证据排多前）；
- 质检：检出率（坏例被拦且类别对）/ 误报率（好例被误拦）。
"""
from __future__ import annotations

from collections.abc import Hashable, Sequence


def precision_recall(gold: Sequence[Hashable], pred: Sequence[Hashable]) -> tuple[float, float, float | None]:
    """精确率 / 召回率 / F1。gold 与 pred 均按去重后的集合比较。

    - 空集合约定：无 gold 且无 pred → (1.0, 1.0)；仅一端空 → 该端分母为 0，0/1 视为 0
      （没检出的召回 0，没预测的精确 0），但解析评测两端通常都非空。
    - F1 在 p+r==0 时为 None（数学未定义），调用方在展示层转 "-"。
    """
    gs, ps = set(gold), set(pred)
    tp = len(gs & ps)
    p = tp / len(ps) if ps else (1.0 if not gs else 0.0)
    r = tp / len(gs) if gs else (1.0 if not ps else 0.0)
    f1 = (2 * p * r / (p + r)) if (p + r) > 0 else None
    return p, r, f1


def recall_at_k(ranked: Sequence[Hashable], gold: Sequence[Hashable], k: int) -> float:
    """Recall@k = top-k 里命中的 gold / gold 总数。gold 必须非空。"""
    assert gold, "Recall@k 要求 gold 非空（无可归因证据的点应单列，不喂本函数）"
    gs = set(gold)
    hit = sum(1 for item in ranked[:k] if item in gs)
    return hit / len(gs)


def mrr_at_k(ranked: Sequence[Hashable], gold: Sequence[Hashable], k: int) -> float:
    """MRR@k = 第一条命中 gold 的倒排名次；top-k 内无命中 → 0。gold 必须非空。"""
    assert gold, "MRR@k 要求 gold 非空"
    gs = set(gold)
    for i, item in enumerate(ranked[:k], start=1):
        if item in gs:
            return 1.0 / i
    return 0.0


def detection_rate(detected: int, total: int) -> float:
    """坏例检出率 = 被正确拦下的坏例 / 已执行的坏例（total=0 → 未定义，返回 0）。"""
    return detected / total if total else 0.0


def fp_rate(false_positive: int, total: int) -> float:
    """好例误报率 = 被误拦的好例 / 好例总数（total=0 → 0）。"""
    return false_positive / total if total else 0.0


def safe_mean(values: Sequence[float], ndigits: int = 4) -> float | None:
    """非空列表均值（空 → None）；round 到 ndigits 位。"""
    if not values:
        return None
    return round(sum(values) / len(values), ndigits)
