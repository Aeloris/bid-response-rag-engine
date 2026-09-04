# -*- coding: utf-8 -*-
"""数值条款抽取：招标要求(→ParamReq) 与我方能力(→OfferClaim)。

哲学（沿用全项目"规则优先、LLM 兜底"）：
- 结构化、够规整的文本走规则（正则 + 同义词对齐），确定性、可离线、可单测；
- 只有"无结构、散落在一段话里"的段落才考虑让 LLM 按 schema 兜底抽一次，
  比较与判定永远不在 LLM 里发生。
- 抽不出/抽不准宁可丢给 UNKNOWN(需人工)，绝不把错数值当能力 —— 宁缺毋滥。

已知局限（写入 docs/calculator.md）：
- 同义词表是"领域字典"，随行业语料扩；条目少了 → 未配对 UNKNOWN（安全方向）；
- 招标"设备行"常是多参数复合句，本项目以"参数名关键词"做一维配对；
  更严谨的(设备×参数)两级键留给接真实语料时按需演进。
"""
from __future__ import annotations

import re

from core.calculator.numeric import parse_numeric, strip_item_prefix
from core.calculator.schemas import OfferClaim, ParamReq

# ---- 同义词表：key(长词优先) → 规范化主题。用于两端配对 ----
_ALIAS: dict[str, str] = {
    # 分辨率 / 像素
    "分辨率": "分辨率", "像素": "分辨率",
    # 质保
    "质保期": "质保期", "质保政策": "质保期", "整机质保": "质保期",
    "质保": "质保期",
    # 工期
    "交付工期": "工期", "交付周期": "工期", "实施周期": "工期",
    "完工期": "工期", "工期": "工期",
    # 接入能力 / 路数
    "接入能力": "接入路数", "接入路数": "接入路数", "路数": "接入路数",
    # 存储周期
    "存储周期": "存储周期", "录像保存时长": "存储周期", "录像存储": "存储周期",
    # 存储容量
    "有效存储容量": "存储容量", "有效容量": "存储容量", "存储容量": "存储容量",
    "容量": "存储容量",
    # 内存
    "内存容量": "内存", "内存": "内存",
    # AI 分析（路数）：行名/AI服务器段落 → AI分析能力，供 "≥100路AI分析" 与产品"分析能力"配对
    "AI分析服务器": "AI分析能力", "分析能力": "AI分析能力", "AI分析": "AI分析能力",
    # CPU
    "处理器": "CPU核心数", "CPU核心数": "CPU核心数", "核心数": "CPU核心数",
    "核数": "CPU核心数",
    # 误报率
    "误报率": "误报率",
    # 照度
    "最低照度": "最低照度", "低照度": "最低照度", "照度": "最低照度",
    # 并发
    "并发用户": "并发用户", "并发数": "并发用户", "并发": "并发用户",
    # 光缆芯
    "光纤芯数": "光缆芯数", "光缆芯数": "光缆芯数", "主干光缆": "光缆芯数",
    "芯数": "光缆芯数",
    # 到场/响应时效（★5.3 重大故障 2 小时内到达）
    "重大故障": "到场时间", "到场": "到场时间", "到达现场": "到场时间",
}

# 允许作为"我方能力"主题的白名单（防止把 "支持/提供" 等噪音标签当能力入表）
KNOWN_TOPICS = {
    "分辨率", "质保期", "工期", "接入路数", "存储周期", "存储容量",
    "内存", "CPU核心数", "误报率", "最低照度", "并发用户", "光缆芯数", "到场时间",
    "AI分析能力",
}

# 无标签裸规格 bullet 的单位兜底（"- 800 万像素（3840×2160）" 这种数值即标签的写法）。
# 只收"单位能唯一确定主题"的：像素→分辨率 无歧义；GB/MB 同时映射 内存/存储 两个主题，
# "内存 128GB" 与 "存储 120TB" 靠前词标签区分，裸 GB 收不了 → 不收（宁漏不硬凑）。
_SPEC_UNIT_TOPIC = {"像素": "分辨率"}

# 标签前后的噪音词（剥掉后对齐同义词）
_NOISE_PREFIX = ("整体", "整机", "有效", "本项目", "我方", "投标人", "平台")
_NOISE_SUFFIX = ("须", "应", "要", "求", "且", "并", "须确保", "达", "至", "支持", "采用", "可")
_CLAUSE_SPLIT = re.compile(r"[；;。，,\n]+")

_CJK_RUN = re.compile(r"[一-龥A-Za-z0-9]+")


def canonical_topic(label: str) -> str:
    """把原始标签对齐到规范化主题；对不上则返回清洗后的标签本身。"""
    if not label:
        return ""
    s = label
    for p in _NOISE_PREFIX:
        if s.startswith(p):
            s = s[len(p):]
            break
    for q in _NOISE_SUFFIX:
        if s.endswith(q):
            s = s[: -len(q)]
            break
    if not s:
        return ""
    # 最长子串优先命中同义词
    best = ""
    for key, canon in sorted(_ALIAS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if key in s:
            return canon
    return s


def split_clauses(text: str) -> list[str]:
    out = []
    for raw in _CLAUSE_SPLIT.split(text):
        c = raw.strip(" 　:-—")
        if c and c != "":
            out.append(c)
    return out


def _label_of_clause(clause: str, numeric) -> str:
    """取数值出现前的最后一个"标签段"，用于对齐主题。"""
    # 与 parse_numeric 同样先剥编号前缀，否则会把 "★5.2" 的编号当数值起点
    s = strip_item_prefix(clause)
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return ""
    before = s[: m.start()]
    runs = _CJK_RUN.findall(before)
    return runs[-1] if runs else ""


def from_tender_doc(doc) -> list[ParamReq]:
    """从解析出的 TenderDoc 抽取招标侧数值要求。

    - tech_params 每行：拆复合句 → 逐句抽有数值的条款 → ParamReq(带 ★ 标记)；
    - star_clauses：★ 条款里的数值句（须含显式比较词，防"付款 30%"误抽）。
    """
    reqs: list[ParamReq] = []

    for row in doc.tech_params:
        clauses = split_clauses(row.requirement)
        found = 0
        for cl in clauses:
            n = parse_numeric(cl, require_comparator=False)
            if n is None:
                continue
            found += 1
            label = _label_of_clause(cl, n)
            topic = canonical_topic(label) or canonical_topic(row.param_name)
            reqs.append(
                ParamReq(
                    id=f"TP-{row.row_no}.{found}",
                    label=label or row.param_name,
                    topic=topic or row.param_name,
                    requirement=cl,
                    numeric=n,
                    star=bool(row.star),
                    source=f"技术参数表第{row.row_no}行",
                )
            )

    for sc in doc.star_clauses:
        clauses = split_clauses(sc.text)
        found = 0
        for cl in clauses:
            n = parse_numeric(cl, require_comparator=True)  # ★ 数值条款须带显式比较词
            if n is None:
                continue
            found += 1
            label = _label_of_clause(cl, n)
            topic = canonical_topic(label)
            reqs.append(
                ParamReq(
                    id=f"{sc.id}-{found}",
                    label=label,
                    topic=topic,
                    requirement=cl,
                    numeric=n,
                    star=True,
                    source=f"{sc.id}（第{sc.page}页）" if sc.page else sc.id,
                )
            )

    return reqs


def from_text(text: str, source: str) -> list[OfferClaim]:
    """从语料文本（产品手册/服务承诺等 Markdown）抽取我方能力声明。

    规则：
    - 逐行扫，跳过 # 标题；兼容 "- 标签：内容" 与 "- 内容" 两种形态；
    - 从句标签能对齐到白名单主题的才算"能力"（防 "支持/提供" 噪音）；
    - 句标签对不上时回退用行头标签（冒号前的段落名）。
    """
    claims: list[OfferClaim] = []
    seen: set[tuple[str, float, str]] = set()

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        body = line[1:].strip() if line.startswith("-") else line
        header = ""
        if "：" in body:
            h, body = body.split("：", 1)
            header = h.strip()
        for cl in split_clauses(body):
            n = parse_numeric(cl, require_comparator=False)
            if n is None:
                continue
            label = _label_of_clause(cl, n)
            topic = canonical_topic(label)
            if topic not in KNOWN_TOPICS and canonical_topic(header) in KNOWN_TOPICS:
                topic = canonical_topic(header)
                label = header
            if topic not in KNOWN_TOPICS and n.unit in _SPEC_UNIT_TOPIC:
                # 句/头标签都对不上，但规格单位能唯一定主题（如 "800 万像素" 裸 bullet）
                topic = _SPEC_UNIT_TOPIC[n.unit]
                label = n.raw
            if topic not in KNOWN_TOPICS:
                continue  # 噪音标签（支持/提供/含…）不入能力表
            key = (topic, round(n.value, 6), n.unit)
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                OfferClaim(
                    id=f"OFF-{len(claims) + 1}",
                    label=label or header or topic,
                    topic=topic,
                    claim=cl,
                    numeric=n,
                    source=f"{source}:{lineno}",
                )
            )
    return claims
