# -*- coding: utf-8 -*-
"""报告浏览与导出路由（Phase 7）：把 job 产物变成给售前审的报告。

- GET /reports                 → HTML 目录页（列出 data/jobs 全部任务，可点开/下载）
- GET /reports/{job_id}        → HTML 报告页（一屏结论/BLOCK 置顶/逐点应答/数值核对/附录）
- GET /reports/{job_id}/export → 下载报告，?fmt=md|xlsx（默认 md）

与 tasks 路由同源：都读 data/jobs/{id}/ 的落盘产物；报告器是纯派生视图，
**绝不重跑引擎 / 不调 LLM**（docs/report.md 概念①）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from app.artifacts import load_job_artifacts
from app.deps import get_job_store
from app.jobs import JobStore
from app.schemas import JobState
from core.reporter.render import (
    render_html,
    render_job_list_html,
    render_markdown,
    render_xlsx_bytes,
)
from core.reporter.service import build_report_from_artifacts

router = APIRouter(prefix="/reports", tags=["reports"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _list_jobs(store: JobStore) -> list[dict]:
    """data/jobs 目录一览（按 state.json 修改时间倒序）。"""
    jobs: list[dict] = []
    for d in store.root.iterdir():
        state_p = d / "state.json"
        if not d.is_dir() or not state_p.exists():
            continue
        try:
            state = JobState.model_validate(json.loads(state_p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 —— 状态文件坏则不显示该行
            continue
        result = store.load_result(d.name)
        jobs.append({
            "job_id": d.name,
            "title": result.tender_title if result else "",
            "status": state.status.value,
            "escalation": bool(result and result.escalation_required),
            "created_at": state.created_at,
            "mtime": state_p.stat().st_mtime,
        })
    jobs.sort(key=lambda j: j["mtime"], reverse=True)
    return jobs


def _report_for(store: JobStore, job_id: str):
    """拿到 job 的产物 → 装配 BidReport；不存在/无产物则抛 404。"""
    if not store.exists(job_id):
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    artifacts = load_job_artifacts(store, job_id)
    if artifacts.result is None:
        raise HTTPException(status_code=404, detail=f"任务尚无产物可报告：{job_id}")
    return build_report_from_artifacts(artifacts)


@router.get("", response_class=HTMLResponse)
async def list_reports(store: JobStore = Depends(get_job_store)) -> HTMLResponse:
    """HTML 目录：全部任务 + 打开/下载链接。"""
    return HTMLResponse(render_job_list_html(_list_jobs(store)))


@router.get("/{job_id}", response_class=HTMLResponse)
async def get_report_html(job_id: str, store: JobStore = Depends(get_job_store)) -> HTMLResponse:
    """HTML 报告页（浏览器阅读、可打印 PDF）。"""
    report = _report_for(store, job_id)
    return HTMLResponse(render_html(report))


@router.get("/{job_id}/export")
async def export_report(
    job_id: str,
    fmt: str = Query("md", pattern="^(md|xlsx)$"),
    store: JobStore = Depends(get_job_store),
) -> Response:
    """下载报告：?fmt=md（正文 markdown）| ?fmt=xlsx（四 sheet 表格）。"""
    report = _report_for(store, job_id)
    if fmt == "xlsx":
        return Response(
            content=render_xlsx_bytes(report),
            media_type=_XLSX_MIME,
            headers={"Content-Disposition": f'attachment; filename="{job_id}.xlsx"'},
        )
    return Response(
        content=render_markdown(report),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.md"'},
    )
