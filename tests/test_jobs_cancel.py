# -*- coding: utf-8 -*-
"""F5 回归：run_pipeline 中途被取消（asyncio.CancelledError，BaseException）必须落盘 FAILED，
不能让它从 `except Exception` 旁边漏过 → state.json 永远停在 running（目录页"幽灵运行中"）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.jobs import JobStore, run_pipeline
from app.schemas import JobStatus

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tender_sample.pdf"


class _CancelLLM:
    """chat 即抛 CancelledError，模拟请求在流水线中途被客户端断开/服务关闭打断。"""

    async def chat(self, messages, *, schema=None):
        raise asyncio.CancelledError()


def test_cancelled_pipeline_persists_failed(tmp_path: Path, settings) -> None:
    assert FIXTURE.exists(), "请先运行 uv run python scripts/make_tender_fixture.py"
    store = JobStore(tmp_path / "jobs")
    job_id = "cancel-01"
    store.create(job_id, FIXTURE.read_bytes())

    with pytest.raises(asyncio.CancelledError):  # 取消信号仍要向上传播（语义不被吞）
        asyncio.run(run_pipeline(
            job_id=job_id,
            pdf_path=store.root / job_id / "input.pdf",
            settings=settings,
            llm=_CancelLLM(),
            store=store,
            corpus_dir=settings.fixtures_path / "corpus",
        ))

    state = store.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.FAILED, "取消后必须落 FAILED，绝不留在 running"
    result = store.load_result(job_id)
    assert result is not None and result.status == JobStatus.FAILED
