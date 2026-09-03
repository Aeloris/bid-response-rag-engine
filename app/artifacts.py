# -*- coding: utf-8 -*-
"""job 工作区 → 强类型产物包（app 层薄 I/O，报告器的唯一数据入口）。

报告器 core/reporter 保持纯函数（不触 IO）；本模块负责把 data/jobs/{id}/ 落盘的
json 读回成强类型对象（JobResult + TenderDoc + PointAnswer[] + ParamCheck[]），
缺哪段就记进 missing，让报告顶部黄条提示 —— 报告可复现，但缺失也要诚实可见。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.calculator.schemas import ParamCheck
from core.generator.schemas import PointAnswer
from core.parser.schemas import TenderDoc
from core.reporter.service import JobArtifacts

from app.jobs import JobStore
from app.schemas import JobResult


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_job_artifacts(store: JobStore, job_id: str) -> JobArtifacts:
    """读一个 job 的全部报告素材；缺哪段记 missing，不抛。"""
    job_dir = store.root / job_id
    steps = job_dir / "steps"
    missing: list[str] = []

    result = store.load_result(job_id)
    if result is None:
        missing.append("result.json")

    doc: TenderDoc | None = None
    raw = _read_json(steps / "01_parse_doc.json")
    if raw is None:
        missing.append("01_parse_doc.json")
    else:
        try:
            doc = TenderDoc.model_validate(raw)
        except Exception:  # noqa: BLE001 —— 坏产物也如实标注，不让报告器炸
            missing.append("01_parse_doc.json(解析失败)")

    answers: list[PointAnswer] = []
    raw = _read_json(steps / "03_gen_answers.json")
    if raw is None:
        missing.append("03_gen_answers.json")
    else:
        try:
            answers = [PointAnswer.model_validate(a) for a in raw]
        except Exception:  # noqa: BLE001
            missing.append("03_gen_answers.json(解析失败)")

    checks: list[ParamCheck] = []
    raw = _read_json(steps / "04_calc_checks.json")
    if raw is None:
        missing.append("04_calc_checks.json")
    else:
        try:
            checks = [ParamCheck.model_validate(c) for c in raw]
        except Exception:  # noqa: BLE001
            missing.append("04_calc_checks.json(解析失败)")

    return JobArtifacts(result=result, doc=doc, answers=answers, checks=checks, missing=missing)


def ensure_result(store: JobStore, job_id: str) -> JobResult | None:
    """仅取 result（存在且可读则返回，否则 None）。"""
    return store.load_result(job_id)
