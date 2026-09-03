# -*- coding: utf-8 -*-
"""检索纯逻辑单测：分词 / BM25 / RRF（不依赖 qdrant，验证核心公式）。"""
from __future__ import annotations

from core.retriever.bm25 import BM25Index
from core.retriever.fusion import rrf_fuse
from core.retriever.tokens import tokenize


def test_tokenize_keeps_ascii_model_and_splits_cjk() -> None:
    toks = tokenize("摄像机400万像素 支持GB28181")
    # 型号整串保留
    assert "gb28181" in toks
    # 中文切成单字+双字，能配到"摄像/像机"
    assert "摄像" in toks and "像机" in toks


def test_bm25_ranks_term_matching_doc_first() -> None:
    docs = [
        "高清网络摄像机 400 万像素 人脸抓拍",
        "网络硬盘录像机 32 路存储",
        "AI 分析服务器 128GB 内存",
    ]
    idx = BM25Index(docs)
    hits = idx.search("400 万像素 人脸抓拍", top_k=3)
    assert hits and hits[0][0] == 0  # 命中第 0 篇


def test_bm25_empty_corpus_ok() -> None:
    assert BM25Index([]).search("任意查询") == []


def test_rrf_prefers_item_ranked_well_in_both_lists() -> None:
    # item 3: dense 第1、bm25 未召回 → 仅一路贡献；item 1: 两路都在前列 → RRF 应更高
    fused = rrf_fuse([3, 1, 4, 2], [1, 2, 3], k=60)
    assert fused[0] == 1, "两路都靠前的元素应排第一"
    assert 3 in fused and 4 in fused
