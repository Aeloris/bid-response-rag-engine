# -*- coding: utf-8 -*-
"""代码判（确定性，Phase 5 三路交叉核对）。LLM 不在本文件出现。

站在"审标人"视角，把生成草稿 × ★条款 × 数值核对叠起来问三个问题，全部可离线、可单测：
1. coverage_issues      ★ / 普通评分点有没有实质应答？应答自报缺材料没有？
2. reconcile_deviation  Phase4 数值核对结论转译：★UNDER→BLOCK、普通 UNDER/UNKNOWN→WARN；
3. numeric_conflicts    应答正文是否自相矛盾（98天 vs 120天）？是否超出我方语料可支撑
                       上限（质保写 5 年而目录仅 3 年，over-commit）？

判不了的语义风险（张冠李戴/不实）留给 judge.py 的 LLM-as-Judge；这里宁缺毋滥、零误报优先。
规则全部复用 Phase 4 的 parse_numeric / canonical_topic / extract.split_clauses，不重复造轮子。
"""
from __future__ import annotations

import re

from core.calculator.extract import (
    KNOWN_TOPICS,
    _ALIAS,
    _NOISE_PREFIX,
    _NOISE_SUFFIX,
    canonical_topic,
    split_clauses,
)
from core.calculator.numeric import _NOISE, parse_numeric, strip_item_prefix
from core.calculator.schemas import OfferClaim, ParamCheck, Verdict
from core.qa.schemas import IssueKind, IssueSeverity, QaIssue

_CJK_RUN = re.compile(r"[一-龥A-Za-z0-9]+")

# 语义"数值越大越优/越小越优"的方向（over-commit 判定用）；默认高者更优
_LOWER_BETTER = {"工期", "到场时间", "最低照度", "误报率"}

# 弱承诺语气词：紧邻数值前出现则视为"可选/约数"而非硬承诺，跳过冲突与超承诺判定
# （"整机质保3年并可延保至5年"的 5 年是可选项；"质保期5年"直白承诺才抓）
# 注意**不放裸"可"**：可承诺/可提供/可满足/可实现 是投标最常见的硬承诺措辞，
# 放了会把"我方可承诺交付工期 70 日历天"这类真承诺当成弱承诺吞掉 → OVER_COMMIT 漏检。
_HEDGE = ("可延保", "可延", "可选", "可扩展至", "支持扩展",
          "加购", "另购", "另行购买", "选购",
          "约", "左右", "近")


# ============================================================ 覆盖率


def coverage_issues(points, answers: list) -> list[QaIssue]:
    """扫描应答覆盖：有没有评分点没答/★没答/应答自报缺材料。

    判定规则（确定性）：
    - 该点评分点没有应答对象、或 answer 为空 → 未实质应答；
      ★(is_star) → BLOCK/UNANSWERED_STAR（不实质响应即废标）；普通点 → WARN/UNANSWERED_POINT。
    - 应答非空但带 missing_evidence → MATERIAL_GAP（承诺了但证明文件待补）；
      ★ 点 → BLOCK，普通点 → WARN。
    """
    by_id = {a.point_id: a for a in answers}
    issues: list[QaIssue] = []

    for p in points:
        a = by_id.get(p.id)
        answered = a is not None and bool(a.answer.strip())
        if not answered:
            note = a.note if (a is not None and a.note) else "该评分点无应答"
            if p.is_star:
                issues.append(
                    QaIssue(
                        id=f"cov-{p.id}",
                        kind=IssueKind.UNANSWERED_STAR,
                        severity=IssueSeverity.BLOCK,
                        point_id=p.id,
                        ref=p.source,
                        evidence=p.content[:80],
                        reason=f"★ 评分点 {p.id} 未实质应答：{note} —— 不实质响应可致废标",
                        fixable=True,
                    )
                )
            else:
                issues.append(
                    QaIssue(
                        id=f"cov-{p.id}",
                        kind=IssueKind.UNANSWERED_POINT,
                        severity=IssueSeverity.WARN,
                        point_id=p.id,
                        ref=p.source,
                        evidence=p.content[:80],
                        reason=f"评分点 {p.id} 未应答：{note} —— 丢分风险",
                        fixable=True,
                    )
                )
            continue

        for ev in a.missing_evidence:
            if p.is_star:
                issues.append(
                    QaIssue(
                        id=f"cov-{p.id}-mat",
                        kind=IssueKind.MATERIAL_GAP,
                        severity=IssueSeverity.BLOCK,
                        point_id=p.id,
                        ref=p.source,
                        evidence=ev,
                        reason=f"★ 点 {p.id} 应答缺证明材料：{ev} —— 需商务/法务补充后才可投",
                    )
                )
            else:
                issues.append(
                    QaIssue(
                        id=f"cov-{p.id}-mat",
                        kind=IssueKind.MATERIAL_GAP,
                        severity=IssueSeverity.WARN,
                        point_id=p.id,
                        ref=p.source,
                        evidence=ev,
                        reason=f"评分点 {p.id} 应答承诺了能力，但证明文件缺失：{ev}",
                    )
                )
    return issues


# ============================================================ 数值偏离复核（转译 Phase 4 结论）


def reconcile_deviation(checks: list[ParamCheck], pending_human: list[str] | None = None) -> list[QaIssue]:
    """把数值核对结论升级为审标风险项（★UNDER 是废标级，必须进风险清单榜首）。

    只做转译，不改判定：Phase 4 用算术判出的 under/unknown，这里按严重级/可追溯性上报告。
    """
    issues: list[QaIssue] = []
    for c in checks:
        v = c.verdict
        req = c.req
        if v == Verdict.UNDER:
            if req.star:
                issues.append(
                    QaIssue(
                        id=f"rec-{req.id}",
                        kind=IssueKind.STAR_UNDER,
                        severity=IssueSeverity.BLOCK,
                        ref=req.source,
                        evidence=f"{req.requirement[:80]}（我方 {c.offer.claim if c.offer else '无'}）",
                        reason=f"★ 参数负偏离：{req.label} —— {c.reason}。不实质响应可致废标，须商务/技术复核",
                    )
                )
            else:
                issues.append(
                    QaIssue(
                        id=f"rec-{req.id}",
                        kind=IssueKind.PARAM_UNDER,
                        severity=IssueSeverity.WARN,
                        ref=req.source,
                        evidence=req.requirement[:80],
                        reason=f"参数负偏离：{req.label} —— {c.reason}",
                    )
                )
        elif v == Verdict.UNKNOWN:
            issues.append(
                QaIssue(
                    id=f"rec-{req.id}",
                    kind=IssueKind.PARAM_UNKNOWN,
                    severity=IssueSeverity.WARN,
                    ref=req.source,
                    evidence=req.requirement[:80],
                    reason=f"{req.label} 无法数值比对：{c.reason}",
                )
            )
    return issues


# ============================================================ 数值自洽 / 超承诺（读应答正文 + 我方能力）


def _topic_near(text: str, pos: int) -> str:
    """取数值前最近的同义词主题（比"整机质保…可延保至 5 年"仍能对齐到 质保期）。

    选"终点最靠近数值"的别名命中；同终点（嵌套别名如 内存容量/容量 都止于同一处）取
    **更长更精确**者 —— 否则"我方服务器内存容量 256GB"会被短别名"容量"归到 存储容量，
    与 extract 侧 canonical_topic("内存容量")→内存 分叉，内存超卖就漏判。
    """
    pre = text[:pos]
    best_key, best_canon, best_end = "", "", -1
    for key, canon in _ALIAS.items():
        i = pre.rfind(key)
        if i < 0:
            continue
        end = i + len(key)
        if end > best_end or (end == best_end and len(key) > len(best_key)):
            best_key, best_canon, best_end = key, canon, end
    if best_key:
        return best_canon
    # 没有可命中的同义词：退回"数值前最后一个词段"清洗
    runs = _CJK_RUN.findall(pre)
    return canonical_topic(runs[-1]) if runs else ""


def _canonical_topic(text: str) -> str:
    """对不在 _ALIAS 的标签也做噪音剥离，但只认 KNOWN_TOPICS（与 extract 口径一致）。"""
    s = text
    for pre in _NOISE_PREFIX:
        if s.startswith(pre):
            s = s[len(pre):]
            break
    for suf in _NOISE_SUFFIX:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s if s in KNOWN_TOPICS else ""


# 数值后主题的向前搜索窗口（"5 年质保""60 个月质保服务"这类 数值+单位+主题 倒装）。
# 只兜底"数值前找不到主题"的情形，且限定在数值后一小段内，防误拾更靠后的独立量主题。
_FORWARD_WINDOW = 16


def _topic_after(base: str, start: int) -> str:
    """数值之后紧邻出现主题词（倒装写法）时取主题；取窗口内最早的同义词命中。"""
    tail = base[start:start + _FORWARD_WINDOW]
    best_key, best_canon, best_start = "", "", len(tail)
    for key, canon in _ALIAS.items():
        i = tail.find(key)
        if i < 0:
            continue
        if i < best_start or (i == best_start and len(key) > len(best_key)):
            best_key, best_canon, best_start = key, canon, i
    if best_key:
        return best_canon
    # 无同义词命中：窗口末一个词段若能对齐白名单主题也认（如 "…5 年最低照度"）
    runs = _CJK_RUN.findall(tail)
    return _canonical_topic(runs[-1]) if runs else ""


def _topic_for(base: str, start: int) -> str:
    """数值定位后取主题（只认白名单 KNOWN_TOPICS，与 offers 池对齐）：
    ① 数值前最近同义词命中（跨措辞如 '…质保…延保至 5 年' 仍对齐质保期）——
       该路径的 canon 必在白名单，直接可用；
    ② 数值前只有噪音标签（"我方承诺 60 个月…"前词是"承诺"这类白名单外词）时，
       再看数值后窗口（"5 年质保"倒装）；仍中不了才退回"数值前最后词段"白名单判定。
    都不中 → 放弃该数量（宁缺毋滥，噪音标签不当主题，避免与能力池对不齐）。"""
    topic = _topic_near(base, start)
    if topic in KNOWN_TOPICS:
        return topic
    after = _topic_after(base, start)
    if after in KNOWN_TOPICS:
        return after
    runs = _CJK_RUN.findall(base[:start])
    return _canonical_topic(runs[-1]) if runs else ""


def _iter_claims(text: str):
    """逐窗口枚举应答正文里的"硬承诺数量"：yield (topic, value, unit, op, raw, is_hedge)。

    每个标点切开的小句里，逐步消费已命中数值后再解析下一个，从而抓住
    "质保 3 年并可延保至 5 年"这类同句多数值。定位用"与 parse_numeric 等长的打码串 +
    数字正则"，而不是回找 n.raw —— raw 不带原文里的空格（"98 日历天" vs "98日历天"），
    直接 find 会在数字与单位带空格时定位失败、漏掉该句全部后续数值。
    噪音（7×24、GB/T…）先整体打码成等长空格，保证定位与 parse_numeric 看到的数一致。
    弱承诺语气（可/约/延保至）判为 hedge，不进冲突/超承诺判定。
    """
    for window in split_clauses(text):
        base = strip_item_prefix(re.sub(r"[（(][^）)]*[）)]", "", window)).strip(" 　:-—")
        if not base:
            continue
        # 等长打码串：噪音数字变空格，真实数量数字原地保留 → 定位索引与 base 一一对应
        mbase = _NOISE.sub(lambda m: " " * len(m.group(0)), base)
        pos = 0
        while pos < len(mbase):
            n = parse_numeric(mbase[pos:], require_comparator=False)
            if n is None or not n.unit:
                break  # 本窗口剩余部分没有"带单位/比较词"的数量了
            dm = re.search(r"\d+(?:\.\d+)?", mbase[pos:])
            if dm is None:
                break
            start = pos + dm.start()
            topic = _topic_for(base, start)
            hedge = any(h in base[max(0, start - 8):start] for h in _HEDGE)
            yield topic, n.value, n.unit, n.operator, n.raw, hedge
            pos = pos + dm.end()  # 越过本数字，让下一轮 parse 找句内后续数量


def _overlap(v1, op1, v2, op2, eps: float = 1e-6) -> bool:
    """两个数量各自隐含的取值集合是否相交；不相交即自相矛盾。

    op=None/"=" 按精确点 [v,v]；严格 "<"/">" 的端点**开**（不含等值）、非严格
    "<="/">=" 端点**闭** —— 否则 "质保期大于 3 年" 与 "质保期不超过 3 年"
    (>36 与 ≤36) 会在等值边界被误判为可共存。相接（一端恰在另一端边界）时
    只有两端都闭才有公共点。
    """
    def _iv(v, op):
        if op in ("<=", "<"):
            return -float("inf"), v, False, op == "<"
        if op in (">=", ">"):
            return v, float("inf"), op == ">", False
        return v, v, False, False  # None / "=" / 未知 → 精确点

    lo1, hi1, lo1_open, hi1_open = _iv(v1, op1)
    lo2, hi2, lo2_open, hi2_open = _iv(v2, op2)

    def _separated(a_hi, a_hi_open, b_lo, b_lo_open) -> bool:
        """区间 a 的上端是否严格在 b 的下端之下 → 两集合不相交。"""
        if a_hi < b_lo - eps:
            return True
        if abs(a_hi - b_lo) <= eps:
            return a_hi_open or b_lo_open  # 恰在边界相接：任一端开 → 无公共点
        return False

    return not (_separated(hi1, hi1_open, lo2, lo2_open)
                or _separated(hi2, hi2_open, lo1, lo1_open))


# offer 侧"可选/加购"弱承诺前缀：紧贴数值前出现则该项不是**硬能力**，不进超承诺上限池。
# 与应答侧 _HEDGE 同一"数值前窗口"思路，但词表更窄：offer 里的"约/可达/支持"仍算可支撑能力
# （如"响应约 30 分钟"仍能佐证 30 分钟级），只有"可延保至/可选/扩展至/选配/加购"这类显式
# 可选项才抬高了上限会骗过 OVER_COMMIT（"质保3年可延保至5年"→ 上限 60，掩盖承诺 4 年已超 36）。
_OFFER_HEDGE_PRE = (
    "可延保至", "可延长至", "可延保", "可延长", "可选",
    "可扩展至", "支持扩展至", "扩展至", "可选购", "选配", "可选配",
    "可选装", "加购", "另购", "需另购", "另行购买",
)


def _offer_is_hedged(o: OfferClaim) -> bool:
    """我方声明是否为显式可选/延保项（弱承诺）——是则不能当"可拍板的硬能力"计入超承诺上限。"""
    text = o.claim or ""
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m is None:
        return False
    # parse_numeric 只认句内**首个**数字；同窗口只查首个数字前，避免把同句"基础值"误伤为弱承诺
    window = text[max(0, m.start() - 12):m.start()]
    return any(h in window for h in _OFFER_HEDGE_PRE)


# 时间量纲跨基准换算（qa 侧自洽/超承诺比对用）：parse_numeric 已把 日→天、年→月 归并，
# 但 分钟/小时 仍是不同基准单位 —— "重大故障 30 分钟内到场" vs 语料"2 小时内到场"
# 不同单位就对不齐，LOWER_BETTER 的"更短更严承诺"会漏判超承诺。这里把 分/时 统一折到
# "分钟"再比（单调线性，不影响方向判定）。**不折 天**：工期/存储周期以天为自然单位，
# 折成分钟会让 bound 变成 "≤141120分钟" 这类无意义大数；天口径内部自比即可（漏的只是
# "N 天内到场" vs "M 小时"这类罕见跨档，宁漏不误导）。
_TIME_UNIT_TO_MIN = {"分钟": 1.0, "小时": 60.0}


def _to_minutes(value: float, unit: str) -> tuple[float, str]:
    """时间类单位折到分钟（否则原样返回）；返回 (换算值, 规范单位)。"""
    f = _TIME_UNIT_TO_MIN.get(unit)
    if f is None:
        return value, unit
    return value * f, "分钟"


def _num(x: float) -> str:
    """数值展示：整数值不带小数点（36.0 → '36'），非整用最简短表达。"""
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def numeric_conflicts(answers: list, offers: list[OfferClaim] | None = None) -> list[QaIssue]:
    """读应答正文，找两类确定性风险：
    - NUM_CONFLICT：同一应答内、同主题同量纲的硬承诺互相矛盾（98 天 vs 120 天）；
    - OVER_COMMIT：硬承诺超出我方语料可支撑的最强能力（质保 5 年而 catalog 仅 3 年）。
    时间类单位先统一折到"分钟"（见 _TIME_UNIT_TO_MIN），跨 分/时/天 措辞才能对齐比较。
    """
    offers_by: dict[tuple[str, str], list[float]] = {}
    for o in offers or []:
        if o.numeric is None or o.topic not in KNOWN_TOPICS:
            continue
        if _offer_is_hedged(o):
            continue  # 可延保至 5 年 / 可选 60 月：是可选项不是硬能力，进池会把上限抬虚、放走超承诺
        val, unit = _to_minutes(o.numeric.value, o.numeric.unit)
        offers_by.setdefault((o.topic, unit), []).append(val)

    issues: list[QaIssue] = []
    for a in answers:
        if not a.answer.strip():
            continue
        claims: list[dict] = []
        for topic, value, unit, op, raw, hedge in _iter_claims(a.answer):
            if hedge or not topic:
                continue
            val, cunit = _to_minutes(value, unit)
            claims.append({"topic": topic, "value": val, "unit": cunit, "op": op, "raw": raw})
        # 同应答内去重（同一数量被引用两次不算矛盾）
        seen_claim: set[tuple] = set()
        uniq: list[dict] = []
        for c in claims:
            k = (c["topic"], round(c["value"], 6), c["unit"], c["op"])
            if k not in seen_claim:
                seen_claim.add(k)
                uniq.append(c)
        claims = uniq

        # ---- 冲突：同 (topic, unit) 的硬承诺集合不相交 ----
        groups: dict[tuple[str, str], list[dict]] = {}
        for c in claims:
            groups.setdefault((c["topic"], c["unit"]), []).append(c)
        for (topic, unit), gs in groups.items():
            for i in range(len(gs)):
                for j in range(i + 1, len(gs)):
                    x, y = gs[i], gs[j]
                    if not _overlap(x["value"], x["op"], y["value"], y["op"]):
                        issues.append(
                            QaIssue(
                                id=f"num-{a.point_id}-{len(issues) + 1}",
                                kind=IssueKind.NUM_CONFLICT,
                                severity=IssueSeverity.WARN,
                                point_id=a.point_id,
                                ref=a.point_id,
                                evidence=f"{x['raw']} vs {y['raw']}",
                                reason=(
                                    f"应答正文对「{topic}」自相矛盾：{x['raw']} 与 {y['raw']} 不可能同时成立，"
                                    "评标专家会视为不实/不专业，建议统一口径"
                                ),
                                fixable=True,
                            )
                        )

        # ---- 超承诺：硬承诺超出我方语料最强能力 ----
        for c in claims:
            pool = offers_by.get((c["topic"], c["unit"]))
            if not pool:
                continue
            if c["topic"] in _LOWER_BETTER:
                best = min(pool)  # 越小越优：我方能保证的最短
                over = c["value"] < best - 1e-9
                bound_txt = f"≤{_num(best)}{c['unit']}（我方能力下限）"
            else:
                best = max(pool)
                over = c["value"] > best + 1e-9
                bound_txt = f"≥{_num(best)}{c['unit']}（我方能力上限）"
            if over:
                issues.append(
                    QaIssue(
                        id=f"ovc-{a.point_id}-{c['topic']}",
                        kind=IssueKind.OVER_COMMIT,
                        severity=IssueSeverity.WARN,
                        point_id=a.point_id,
                        ref=a.point_id,
                        evidence=c["raw"],
                        reason=(
                            f"应答承诺 {c['topic']} {c['raw']}，超出我方语料可支撑的 {bound_txt} —— "
                            "开标/答疑拿不出支撑即失信"
                        ),
                        fixable=True,
                    )
                )
    return issues
