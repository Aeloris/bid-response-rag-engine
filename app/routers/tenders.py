# -*- coding: utf-8 -*-
"""招标书解析路由：先让用户看评分点/★/参数表，再决定要不要跑全文 pipeline。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.deps import get_llm, get_settings
from app.schemas import ParseOutcome, parse_outcome
from config.settings import Settings
from core.parser.pipeline import parse_tender

router = APIRouter(prefix="/tenders", tags=["tenders"])


async def _read_pdf(file: UploadFile) -> bytes:
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="仅支持 PDF（文件头须为 %PDF）")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 50MB 上限")
    return data


@router.post("/parse", response_model=ParseOutcome)
async def parse_tender_file(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    llm: object = Depends(get_llm),
) -> ParseOutcome:
    """上传招标书 PDF → 解析出评分点/★条款/参数表速览（同步，供人工先确认）。"""
    data = await _read_pdf(file)
    uploads = Path(settings.data_path) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    tmp = uploads / f"{uuid.uuid4().hex}.pdf"
    tmp.write_bytes(data)
    try:
        doc, report = await parse_tender(tmp, llm)
    except HTTPException:
        raise
    except (ValueError, OSError, RuntimeError) as exc:
        # 输入级解析失败（PDF 无页面/损坏/无文本层等，fitz/loader 抛 ValueError、OSError、
        # RuntimeError 系异常）→ 4xx，不让它 500。内部代码 bug 属其它异常类型仍按 500 如实暴露。
        raise HTTPException(
            status_code=400,
            detail=f"该 PDF 无法解析（损坏/无页面/无文本层/非有效 PDF）：{type(exc).__name__}: {exc}",
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return parse_outcome(doc, report)
