# -*- coding: utf-8 -*-
"""任务路由：一条标 = 一个 job。

- POST /tasks：上传招标书 PDF → 建任务工作区 → 同步跑 parse→…→qa → 返回终态 JobState；
- GET  /tasks/{id}        轮询状态（pending/running/done/failed + 当前 step）；
- GET  /tasks/{id}/result 拉整条产物（gen/calc/qa 三份总结 + 待补材料 + 拦截标记）。

v1 同步执行（uvicorn 单 worker 够 demo/单用户）；产物已逐段落盘 data/jobs/{id}/，
后续切 BackgroundTasks/队列只改这里，不动引擎。局限见 docs/api.md。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.deps import get_job_store, get_llm, get_settings
from app.jobs import JobStore, run_pipeline
from app.schemas import JobResult, JobState, JobStatus
from app.routers.tenders import _read_pdf
from config.settings import Settings

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=JobState, status_code=202)
async def create_task(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    llm: object = Depends(get_llm),
    store: JobStore = Depends(get_job_store),
) -> JobState:
    """上传招标书 → 建 job 工作区 → 跑完整条 pipeline（同步）→ 返回终态。"""
    data = await _read_pdf(file)
    job_id = uuid.uuid4().hex[:12]
    store.create(job_id, data)

    pdf_path = store.root / job_id / "input.pdf"
    await run_pipeline(job_id=job_id, pdf_path=pdf_path, settings=settings,
                       llm=llm, store=store, corpus_dir=settings.fixtures_path / "corpus")
    state = store.get_state(job_id)
    assert state is not None
    return state


@router.get("/{job_id}", response_model=JobState)
async def get_task(job_id: str, store: JobStore = Depends(get_job_store)) -> JobState:
    """轮询任务状态；失败时 state.error 含出错步骤与原因。"""
    state = store.get_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    return state


@router.get("/{job_id}/result", response_model=JobResult)
async def get_task_result(job_id: str, store: JobStore = Depends(get_job_store)) -> JobResult:
    """拉取一条标产物；done 才有完整 gen/calc/qa，failed 也有结构化错误与已跑到步。"""
    if not store.exists(job_id):
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    result = store.load_result(job_id)
    if result is None:
        state = store.get_state(job_id)
        if state and state.status == JobStatus.RUNNING:
            raise HTTPException(status_code=409, detail=f"任务仍在执行：{state.step}")
        raise HTTPException(status_code=404, detail=f"任务尚无产物：{job_id}")
    return result
