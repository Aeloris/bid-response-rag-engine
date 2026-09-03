# -*- coding: utf-8 -*-
"""数值解析与比对核心（纯代码，不碰 LLM）。

本文件是 Phase 4 的"计算器"——把文字里的数量条款解析成可比的量，并按比较方向判定
是否达标。核心原则：
1. **能算的绝不让模型算**：≥/≤、单位换算、边界判定全部走这里的确定性代码。
2. **防误抽优先**：7×24、1/1.8英寸、GB/T28181、2560×1440 这类"看似数字实则不是
   比较量"的模式一律不抽 —— 宁可漏判(留 UNKNOWN/Phase5)，绝不把没关系的数当参数值。
3. **量纲一致才比**：比较前两边都折算到基准单位；量纲对不上 → 不硬比（UNKNOWN）。

单位/同义词表放这里（代码内）而非 config：它们是"领域字典/判定逻辑"，改了就要重新
测，属于代码而非运行时开关；外置到 YAML 反而失去类型与可测性。理由写在此处备查。
"""
from __future__ import annotations

import re

from core.calculator.schemas import NumericValue

# ---- 单位表：显示词 → (基准单位, 折算系数)。注意按长度降序匹配，长词优先 ----
_UNITS: list[tuple[str, str, float]] = [
    ("日历天", "天", 1.0),  # 日历天 ≡ 天
    ("万像素", "像素", 1e4),  # 兜底写法（主走 万 倍数 + 像素）
    ("分钟", "分钟", 1.0),
    ("个小时", "小时", 1.0),
    ("小时", "小时", 1.0),
    ("万路", "路", 1e4),
    ("个月", "月", 1.0),
    ("年", "月", 12.0),
    ("月", "月", 1.0),
    ("天", "天", 1.0),
    ("日", "天", 1.0),
    ("像素", "像素", 1.0),
    ("路", "路", 1.0),
    ("核", "核", 1.0),
    ("芯", "芯", 1.0),
    ("张", "张", 1.0),
    ("人", "人", 1.0),
    ("台", "台", 1.0),
    ("套", "套", 1.0),
    ("个", "个", 1.0),
    ("TB", "MB", 1024.0 * 1024.0),
    ("T", "MB", 1024.0 * 1024.0),
    ("GB", "MB", 1024.0),
    ("G", "MB", 1024.0),
    ("MB", "MB", 1.0),
    ("M", "MB", 1.0),
    ("Kbps", "Kbps", 1.0),
    ("Lux", "Lux", 1.0),
    ("lux", "Lux", 1.0),
    ("帧", "帧", 1.0),
    ("％", "%", 1.0),
    ("%", "%", 1.0),
    ("℃", "℃", 1.0),
    ("°", "℃", 1.0),
]
# 单位匹配用的词（长→短），ASCII 单位需 \b 边界
_UNIT_WORDS = sorted((w for w, *_ in _UNITS), key=len, reverse=True)
_UNIT_RE = re.compile(
    r"(?P<unit>" + "|".join(re.escape(w) for w in _UNIT_WORDS) + r")",
    re.IGNORECASE,
)

# ---- 比较词（符号 & 中文语义），映射成程序化 op。长词优先，防"不小于"误读成"不/小于" ----
_CMP_PRE = [
    ("不得低于", ">="), ("不得小于", ">="), ("不少于", ">="), ("不小于", ">="),
    ("不低于", ">="), ("至少", ">="), ("不小于", ">="),
    ("不得超过", "<="), ("不大于", "<="), ("不多于", "<="), ("不超过", "<="),
    ("不高于", "<="), ("至少不高于", "<="), ("须不高于", "<="), ("以内", "<="),
    ("以内不高于", "<="), ("不迟于", "<="),
    ("大于", ">"), ("高于", ">"), ("多于", ">"),
    ("小于", "<"), ("低于", "<"), ("少于", "<"),
    ("不超过", "<="),
]
_CMP_PRE.sort(key=lambda x: len(x[0]), reverse=True)
_CMP_POST = [  # 后缀比较词：出现在数值之后（注意：裸"内"不放这里，避免误伤"内存/内容"）
    ("及以上", ">="), ("以上", ">="), ("或以上", ">="), ("至少", ">="),
    ("及以下", "<="), ("以下", "<="), ("以内", "<="),
]
_CMP_POST.sort(key=lambda x: len(x[0]), reverse=True)
# "内"只有紧跟在时间单位后才是上界（"2小时内""120日历天内"），用邻接检测而非子串
_ADJ_TIME_UPPER = re.compile(r"[天时月年日分秒]内")
_CMP_SYMBOL = re.compile(r"(>=|<=|==|>|<|=|≥|≤)")

# 多倍率："400万像素" = 400 × 1e4；"120万"同。万/百万
_MULT = [("百万", 1e6), ("万", 1e4), ("千", 1e3)]
_MULT.sort(key=lambda x: len(x[0]), reverse=True)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 会被"假装成数字"但不该抽的干扰模式（在 clause 里命中即跳过该段）
_NOISE = re.compile(
    r"(GB/T|IEEE|ISO|GB\s*[0-9]|HC-IPC|\d+\s*[xX×]\s*\d+|\d+/\d+|[a-zA-Z]{1,4}\d{2,})"
)


def _strip_paren(s: str) -> str:
    """去掉括号内容（规格说明里的 2560×1440、按…估算 等常在括号里），避免被当成主数量。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return s


def strip_item_prefix(s: str) -> str:
    """去掉条款/条目编号前缀（"★5.2 项目交付工期…"、"5.2 …"）。

    编号里的点号极易被当成数值（如把"★5.2"的 5.2 当成本条款数值）。
    解析数值与取标签都必须先做这一步 → 提取到数值处（_label_of_clause 也用本函数）。

    注意第二条只对"带小数点 + 后跟空格 + CJK 标题"生效（如 "5.2 项目"），
    **绝不剥** 整数量词开头（"24 芯"、"3 年" 都是真实规格，不是编号）。
    """
    s = re.sub(r"^[★☆*]+\s*\d+(?:[.．、]\d+)*[\s]*", "", s)
    s = re.sub(r"^\d+[.．、]\d+\s+(?=[一-龥])", "", s)
    return s


def parse_numeric(text: str, *, require_comparator: bool = False) -> NumericValue | None:
    """从句子里解析出第一个"可比较的数量条款"。

    规则（确定性，按序）：
    1. 清洗：去括号 + 去编号前缀；对噪音模式（7×24、1/1.8、GB/T28181、HC-IPC400W…）
       就地打码，使它们不可能被误当成数字；
    2. 找比较词：词/符号出现在数值前，或"内/以上"出现在数值后 → op；
    3. 找数值：首个 数字(带可选小数)；允许紧邻的 万/百万 倍率 与 单位；
    4. 折算到基准单位（value = 数 × 倍率 × 单位系数）；
    5. 无数值 / 无单位且无数值比较词（require_comparator 语义：比较词必须显式）→ None。

    require_comparator=True 用于"★ 数值条款"抽取：付款 30% 这类无比较词的百分比
    不属于达标型要求，不抽，避免把"金额里程碑"误当能力阈值。
    """
    if not text:
        return None
    s = strip_item_prefix(_strip_paren(text))

    # 1) 噪音打码：把干扰段整体替换为空格，防误抽
    def _mask(m: re.Match) -> str:
        return " " * len(m.group(0))

    s = _NOISE.sub(_mask, s)

    # 2) 比较词定位
    operator: str | None = None
    for w, op in _CMP_PRE:
        if w in s:
            operator = op
            break
    m_sym = _CMP_SYMBOL.search(s)
    if m_sym:
        sym = m_sym.group(1)
        operator = {"≥": ">=", "≤": "<=", "==": "="}.get(sym, sym)
    # 后缀比较词（在数字之后，如 "2小时内" → ≤2小时、"400万以上" → ≥）
    post_op: str | None = None
    for w, op in _CMP_POST:
        if w in s:
            post_op = op
            break
    # 时间单位紧邻的"内" = 上界（"2小时内""120日历天内"），邻接检测防误伤"内存/内容"
    if post_op is None and operator is None and _ADJ_TIME_UPPER.search(s):
        post_op = "<="
    if operator is None and post_op is not None:
        # 只有后缀词时，也先按后缀词给个方向（如 "以上"→">="、"内"→"<="）
        operator = post_op

    # 3) 数值 + 倍率 + 单位
    body = s
    m = _NUM_RE.search(body)
    if not m:
        return None
    num = float(m.group(0))
    after = body[m.end():]

    # 倍率（紧跟在数字后，如 "400万像素"）
    mult = 1.0
    for w, f in _MULT:
        if after.startswith(w):
            mult = f
            after = after[len(w):]
            break
    # 单位（可隔着空格，如 "≥32 路"）
    after = after.lstrip(" 　")
    um = _UNIT_RE.match(after)
    raw_unit_word = um.group("unit") if um else ""
    if raw_unit_word:
        for w, base, factor in _UNITS:
            if w.lower() == raw_unit_word.lower():
                unit, factor = base, factor
                break
        else:  # 理论上不会到这里
            unit, factor = raw_unit_word.lower(), 1.0
    else:
        unit = ""  # 无数值单位：仅当有显式比较词才当作"纯数量"接受
        factor = 1.0

    # 语义边界判定：有没有资格成为一个"可比较量"？
    has_explicit_cmp = operator is not None or post_op is not None
    if not has_explicit_cmp:
        if not unit:
            return None  # 无数值词又无单位 → 不是数量条款（如 "GB/T28181" 已被打码）
        # 有单位但无数值比较词：可能是产品规格裸值（如 "分辨率 400 万像素"）
        if require_comparator:
            return None  # ★ 要求侧：无比较词不抽（付款 30% 等不达标型）
    # 后缀词覆盖前缀判断：如 "2小时内" 前缀无词，operator 已由后缀设为 "<="

    value = num * mult * factor
    raw_clip = m.group(0) + (("万" if mult == 1e4 else "百万" if mult == 1e6 else "") if mult != 1.0 else "")
    if raw_unit_word:
        raw_clip += raw_unit_word
    return NumericValue(value=value, unit=unit, operator=operator, raw=raw_clip)


def same_dimension(a: NumericValue, b: NumericValue) -> bool:
    """两边基准单位是否同一量纲（都可为空字符串=纯数量）。"""
    return a.unit == b.unit


# 判定输出：verdict + 一句话 reason 素材


def compare_numeric(req: NumericValue, offer: NumericValue) -> tuple[str, str]:
    """确定性判定：招标要求 req vs 我方声明 offer → (verdict, 说明)。

    语义（面向"我方值 = 可达能力"）：
    - req.operator 缺省按 "="（须等于/满足该规格）；offer 数值视作我方声明值本身；
    - 对每种比较方向判定 达标(CONFORM) / 更优(OVER) / 不达标(UNDER)。
      恰在边界（含浮点容差）一律判 CONFORM 而非 OVER/UNDER，避免对等值误报。
    """
    req_op = req.operator if req.operator is not None else "="
    rv, ov, ru, ou = req.value, offer.value, req.unit, offer.unit
    eps = 1e-9
    req_txt = f"{rv}{ru}" if not req.operator else f"{req.operator}{rv}{ru}"
    off_txt = f"{ov}{ou}"

    if req_op in (">=", ">"):
        if ov > rv + eps:
            return ("over", f"我方 {off_txt} 超出 要求{req_txt}")
        if ov >= rv - eps:  # 等值边界 → 满足
            return ("conform", f"我方 {off_txt} 达边界 要求{req_txt}")
        return ("under", f"我方 {off_txt} 达不到 要求{req_txt}")
    if req_op in ("<=", "<"):
        if ov < rv - eps:
            return ("over", f"我方 {off_txt} 优于(更小) 要求{req_txt}")
        if ov <= rv + eps:
            return ("conform", f"我方 {off_txt} 在限 要求{req_txt} 内")
        return ("under", f"我方 {off_txt} 超上限 要求{req_txt}")
    # "=" 精确匹配
    if abs(ov - rv) <= eps:
        return ("conform", f"我方 {off_txt} 等于 要求 {rv}{ru}")
    return ("under", f"我方 {off_txt} ≠ 要求 {rv}{ru}（规格偏离）")
