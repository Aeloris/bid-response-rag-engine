# -*- coding: utf-8 -*-
"""中文轻量分词（纯 Python，无 jieba 大依赖）。

背景：BM25/词法重排需要"词"。英文按空白切即可；中文没有天然词边界，正规做法上 jieba，
但为省依赖 + 可控，这里用字符级 n-gram（1~2 gram）近似：
- 对连续中文/通用字符滑窗取 1、2-gram；
- 对连续的英文数字型号（如 gb28181、400w、rtsp）单独保留整串小写（型号必须整段匹配，不能切碎）。

局限（面试诚实点）：字符 n-gram 不如词典分词语义干净，但检索/重排做"词面匹配"足够，
且**文档与 query 走同一函数 → 两边 token 恒对齐**，排名行为确定可测。
"""
from __future__ import annotations

import re

_CJK_RUN = re.compile(r"[0-9a-zA-Z.\-≥≤/·%]+|[一-鿿]+|[^\s一-鿿0-9a-zA-Z]+")


def tokenize(text: str) -> list[str]:
    """把文本切成检索用 token 列表。"""
    tokens: list[str] = []
    for seg in _CJK_RUN.findall(text.lower()):
        if seg[0].isascii():
            # 英数型号串（可能含点杠百分比等符号）整段保留，型号必须整段匹配
            tokens.append(seg)
            continue
        chars = list(seg)
        tokens += chars  # 单字：保证"摄像/机"这类词素可配
        tokens += [seg[i : i + 2] for i in range(len(seg) - 1)]  # 相邻双字
    return tokens
