# -*- coding: utf-8 -*-
"""第四轮终审回归：空解析门禁 + 文件即状态的损坏容忍（原子写/容错读/诚实降级）。

修复对象（git log 见 README 变更记录）：
- 空解析假绿：0 评分点（扫描件/无文本层/规则未命中）原先会走完流水线 → DONE → 报告假绿"可投"，
  现门禁在 parse 段判 failed；
- state.json / result.json 读取容错：截断/外部改坏 → 降级为"失败+可读原因"（不 500、不让目录页整页崩）；
- 写侧原子化（同目录 .tmp + os.replace），正常写路径不产生半截 JSON；
- /tenders/parse 对坏 PDF（fitz/loader 抛 ValueError/OSError/RuntimeError）转 4xx，引擎级 bug 仍如实 500。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.jobs import JobStore, run_pipeline
from app.routers.reports import _list_jobs, _report_for
from app.schemas import JobResult, JobStatus
from app.routers import tenders as tenders_router

REPO_ROOT = Path(__file__).resolve().parents[1]


def _blank_pdf(path: Path) -> None:
    """生成一份"有效但无任何评分栏目"的 PDF：正文无 第X章/节 标题 → 规则层零命中。"""
    try:  # PyMuPDF ≥1.24 顶层模块名 pymupdf，老版本走 fitz 别名（与 core/parser/loader 同策略）
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72),
                     "Chapter A. This page intentionally contains no tender scoring section "
                     "and no chapter headings, to exercise the empty-parse guard.",
                     fontsize=12)
    doc.save(str(path))
    doc.close()


# ------------------------------------------------------------ 1. 空解析门禁
def test_empty_parse_marks_job_failed_not_green(tmp_path: Path, settings) -> None:
    """0 评分点（无文本层/扫描件/规则未命中）→ 必须 FAILED@parse，不许空结果冒充可投。"""
    pdf = tmp_path / "blank.pdf"
    _blank_pdf(pdf)

    store = JobStore(tmp_path / "jobs")
    job_id = "empty-parse-01"
    store.create(job_id, pdf.read_bytes())

    result = asyncio.run(run_pipeline(
        job_id=job_id,
        pdf_path=store.root / job_id / "input.pdf",
        settings=settings,
        llm=object(),  # 空解析根本不调用 LLM（规则层零命中即返回 ok=False）
        store=store,
        corpus_dir=settings.fixtures_path / "corpus",
    ))
    assert result.status == JobStatus.FAILED
    assert result.step == "parse"
    assert "评分点" in result.error
    state = store.get_state(job_id)
    assert state is not None and state.status == JobStatus.FAILED


# ------------------------------------------------------------ 2. state.json 损坏降级
def test_corrupt_state_json_degrades_to_failed(tmp_path: Path) -> None:
    """state.json 截断 → get_state 降级为 FAILED(可读原因)，不抛、不 500、不消失。"""
    store = JobStore(tmp_path / "jobs")
    d = store.root / "corrupt-state"
    d.mkdir(parents=True)
    (d / "state.json").write_text('{"job_id": "corrupt-state", "status": "done"', encoding="utf-8")  # 截断 JSON

    state = store.get_state("corrupt-state")  # 不抛
    assert state is not None
    assert state.status == JobStatus.FAILED
    assert "损坏" in state.error
    assert "corrupt-state" in state.error


def test_reports_list_shows_corrupt_job_as_failed_row(tmp_path: Path) -> None:
    """目录页对损坏 state.json 显示红标 failed 行（诚实可见），而不是跳过/整页崩。"""
    store = JobStore(tmp_path / "jobs")

    # 好任务：正常 create + update done
    store.create("good-job", b"%PDF fake-bytes-for-io")
    store.update("good-job", status=JobStatus.DONE, step="done")

    # 坏任务：手写截断 state.json
    bad = store._dir("corrupt-state")
    (bad / "state.json").write_text('{"job_id": ', encoding="utf-8")

    rows = _list_jobs(store)  # 不抛
    by_id = {r["job_id"]: r for r in rows}
    assert "good-job" in by_id and by_id["good-job"]["failed"] is False
    assert "corrupt-state" in by_id
    assert by_id["corrupt-state"]["status"] == "failed"
    assert by_id["corrupt-state"]["failed"] is True


# ------------------------------------------------------------ 3. result.json 损坏容忍
def test_corrupt_result_json_reads_none_and_report_is_404(tmp_path: Path, settings) -> None:
    """result.json 截断 → load_result 返回 None（不抛）；报告页/下载按"无产物"走 404，不是 500。"""
    store = JobStore(tmp_path / "jobs")
    store.create("broken-result", b"%PDF fake-bytes-for-io")
    store.update("broken-result", status=JobStatus.DONE, step="done")
    # 写一个合法结构再截断，模拟写入后磁盘/外部损坏
    (store.root / "broken-result" / "result.json").write_text(
        '{"job_id": "broken-result", "status": "done", "score_points": ', encoding="utf-8"
    )

    assert store.load_result("broken-result") is None  # 容错：不抛
    with pytest.raises(HTTPException) as ei:
        _report_for(store, "broken-result")
    assert ei.value.status_code == 404


def test_load_result_tolerates_pydantic_mismatch(tmp_path: Path) -> None:
    """result.json 是合法 JSON 但字段形状不对（外部改写/旧版本残留）→ 同样按 None，不抛 pydantic。"""
    store = JobStore(tmp_path / "jobs")
    store.create("shape-mismatch", b"%PDF fake-bytes-for-io")
    store.update("shape-mismatch", status=JobStatus.DONE, step="done")
    (store.root / "shape-mismatch" / "result.json").write_text(
        '{"unexpected": "schema", "job_id": 12345}', encoding="utf-8"
    )
    assert store.load_result("shape-mismatch") is None


# ------------------------------------------------------------ 4. /tenders/parse 错误映射
def _install_parse_raising(monkeypatch, exc_cls: type[BaseException]):
    async def _boom(*args, **kwargs):
        raise exc_cls(f"synthetic {exc_cls.__name__}")
    monkeypatch.setattr(tenders_router, "parse_tender", _boom)


def test_parse_preview_bad_pdf_400_not_500(monkeypatch) -> None:
    """fitz/loader 的输入级异常（ValueError/OSError/RuntimeError 系）→ 400，不让坏 PDF 打成 500。"""
    from fastapi.testclient import TestClient
    from app.main import app

    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ndummy-header-passes-magic-check"
    with TestClient(app) as client:
        for exc_cls in (ValueError, RuntimeError, OSError):
            _install_parse_raising(monkeypatch, exc_cls)
            resp = client.post("/tenders/parse", files={"file": ("bad.pdf", pdf_bytes, "application/pdf")})
            assert resp.status_code == 400
            assert resp.status_code < 500
            assert "无法解析" in resp.json()["detail"]


def test_parse_preview_internal_bug_still_500(monkeypatch) -> None:
    """非输入级的引擎 bug（如 TypeError）必须如实 500，不能被误标成"用户 PDF 坏"而吞掉。"""
    from fastapi.testclient import TestClient
    from app.main import app

    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ndummy-header-passes-magic-check"
    _install_parse_raising(monkeypatch, TypeError)
    with TestClient(app) as client:
        with pytest.raises(TypeError):  # raise_server_exceptions 默认把引擎 bug 抛回测试 → 证明未被 400 掩盖
            client.post("/tenders/parse", files={"file": ("bad.pdf", pdf_bytes, "application/pdf")})


# ------------------------------------------------------------ 5. 原子写
def test_atomic_write_leaves_no_tmp_leftovers(tmp_path: Path, settings) -> None:
    """state/result/steps 全部经 .tmp+os.replace：写完后工作区无 *.tmp 残留，JSON 可读。"""
    store = JobStore(tmp_path / "jobs")
    job_id = "atomic-01"
    store.create(job_id, b"%PDF fake")
    store.update(job_id, status=JobStatus.DONE, step="done")
    store.save_result(job_id, JobResult(job_id=job_id, status=JobStatus.DONE, step="done"))
    store.save_step(job_id, "05_qa_report", {"ok": True})

    d = store.root / job_id
    leftovers = [p.name for p in d.glob("*.tmp")] + [p.name for p in (d / "steps").glob("*.tmp")]
    assert leftovers == []
    # 产物都能读回（写没有截断）
    assert store.get_state(job_id) is not None
    assert store.load_result(job_id) is not None
    assert json.loads((d / "steps" / "05_qa_report.json").read_text(encoding="utf-8")) == {"ok": True}
