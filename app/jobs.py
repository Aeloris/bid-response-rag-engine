# -*- coding: utf-8 -*-
"""任务（Job）管理：一条标的落盘工作区 + 五引擎流水线编排。

设计要点（为什么不是同步裸调用，见 docs/api.md）：
- 一条标 = parse→ingest→generate→calc→qa，动辄秒级、有状态(建库)，塞进一个同步请求
  会超时、会抹掉"卡在哪一步"。所以 JobStore 给每个 job 一个 data/jobs/{id}/ 工作区：
  input.pdf + state.json + result.json + steps/ 每步产物 —— 可轮询、可审计、Phase 8 可回放。
- run_pipeline 逐引擎打日志、逐引擎产物落盘；任一步抛错 → 状态 failed + 明确指出"哪一步 +
  原因"，而不是让整体 500（错误分段，不吞引擎的诚实信号）。
- 已知局限（写入 docs/api.md）：v1 单进程 dict 状态 + 同步执行（uvicorn 单 worker 够 demo）；
  多 worker/横向扩展要换存储与任务队列。
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from pathlib import Path

from config.settings import Settings
from core.calculator import Calculator, extract as calc_extract
from core.generator import Generator
from core.ingest import ingest_corpus
from core.parser.pipeline import parse_tender
from core.qa import QaService
from core.retriever import Retriever
from core.vector_store import VectorStore

from app.schemas import (
    JobResult,
    JobState,
    JobStatus,
    parse_outcome,
    score_points_brief,
)

# 语料文件约定（docs 与 demo 沿用）：产品手册 / 资质与服务 / 案例
_CORPUS_FILES = ("product-guide.md", "qualifications-and-service.md", "cases.md")


class JobStore:
    """文件即状态：data/jobs/{job_id}/ 下 input.pdf + state.json + result.json + steps/。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, job_id: str) -> Path:
        d = self.root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- 状态 ----
    def create(self, job_id: str, pdf_bytes: bytes) -> JobState:
        d = self._dir(job_id)
        (d / "input.pdf").write_bytes(pdf_bytes)
        state = JobState(job_id=job_id, status=JobStatus.PENDING,
                         step="created", created_at=self._now())
        self._write_state(state)
        return state

    def update(self, job_id: str, *, status: JobStatus | None = None,
               step: str | None = None, error: str | None = None) -> JobState:
        cur = self._read_state(job_id)
        state = JobState(
            job_id=job_id,
            status=status or cur.get("status", JobStatus.PENDING.value),
            step=step if step is not None else cur.get("step", ""),
            error=error if error is not None else cur.get("error", ""),
            created_at=cur.get("created_at", self._now()),
        )
        self._write_state(state)
        return state

    def get_state(self, job_id: str) -> JobState | None:
        cur = self._read_state(job_id)
        if not cur:
            return None
        return JobState.model_validate(cur)

    def exists(self, job_id: str) -> bool:
        return (self.root / job_id / "state.json").exists()

    # ---- 产物 ----
    def save_result(self, job_id: str, result: JobResult) -> None:
        d = self._dir(job_id)
        (d / "result.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )

    def load_result(self, job_id: str) -> JobResult | None:
        p = self.root / job_id / "result.json"
        if not p.exists():
            return None
        return JobResult.model_validate_json(p.read_text(encoding="utf-8"))

    def save_step(self, job_id: str, name: str, obj) -> None:
        """每步产物落盘（审计 / Phase8 回放）。obj 可以是 pydantic 或 dict。"""
        d = self.root / job_id / "steps"
        d.mkdir(parents=True, exist_ok=True)
        if hasattr(obj, "model_dump"):
            payload = obj.model_dump(mode="json")
        else:
            payload = obj
        (d / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 内部 ----
    def _write_state(self, state: JobState) -> None:
        (self._dir(state.job_id) / "state.json").write_text(
            state.model_dump_json(indent=2), encoding="utf-8"
        )

    def _read_state(self, job_id: str) -> dict:
        p = self.root / job_id / "state.json"
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")


async def run_pipeline(
    *,
    job_id: str,
    pdf_path: Path,
    settings: Settings,
    llm: object,
    store: JobStore,
    corpus_dir: Path | None = None,
) -> JobResult:
    """按序跑 parse→ingest→generate→calc→qa，逐段落盘；任一段失败即分段返回。

    错误分段哲学：不吞引擎的诚实信号 —— 引擎自己给的 gap/unknown/BLOCK 都留在产物里；
    只有"代码真的抛异常"才判 failed，并注明是哪一步、为什么。
    """
    store.update(job_id, status=JobStatus.RUNNING)
    corpus_dir = Path(corpus_dir) if corpus_dir else settings.fixtures_path / "corpus"
    result = JobResult(job_id=job_id)

    step = "parse"  # 当前步骤名：失败时精确到段
    try:
        # ---- 1. 解析 ----
        store.update(job_id, step="parse")
        doc, report = await parse_tender(pdf_path, llm)
        store.save_step(job_id, "01_parse_doc", doc)
        store.save_step(job_id, "01_parse_report", report)
        result.tender_title = doc.tender_title
        result.score_points = score_points_brief(doc)

        # ---- 2. 入库（本地向量库，进程内）----
        step = "ingest"
        store.update(job_id, step="ingest")
        vs = VectorStore(settings.vector_db.collection, settings.embedding.dimension, path=":memory:")
        await ingest_corpus(corpus_dir, vs, settings)

        # ---- 3. 生成应答（Phase3）----
        step = "generate"
        store.update(job_id, step="generate")
        retriever = Retriever(settings, vs)
        gen = Generator(settings, llm)
        answers, result.gen = await gen.generate(
            doc.score_points, retriever.retrieve, doc.tender_title)
        store.save_step(job_id, "03_gen_summary", result.gen)
        store.save_step(job_id, "03_gen_answers",
                        [a.model_dump(mode="json") for a in answers])

        # ---- 4. 数值核对（Phase4）----
        step = "calculate"
        store.update(job_id, step="calculate")
        reqs = calc_extract.from_tender_doc(doc)
        offers = _load_offers(corpus_dir)
        checks, result.calc = Calculator(settings).check(reqs, offers)
        store.save_step(job_id, "04_calc_summary", result.calc)
        store.save_step(job_id, "04_calc_checks",   # 每行核对明细：Phase 7 报告器展示用
                        [c.model_dump(mode="json") for c in checks])

        # ---- 5. 自检质检（Phase5）----
        step = "qa"
        store.update(job_id, step="qa")
        qa = QaService(settings, llm)
        qrep, _ = await qa.run(
            points=doc.score_points,
            answers=answers,
            checks=checks,
            offers=offers,
            tender_title=doc.tender_title,
            buyer=doc.buyer,
            deadline=doc.deadline,
        )
        result.qa = qrep
        store.save_step(job_id, "05_qa_report", qrep)
    except asyncio.CancelledError:
        # 请求被取消（客户端断开 / 服务关闭）会以 BaseException 传播，`except Exception`
        # 兜不住 → 若直接让它逃逸，state.json 永远停在 running，目录页出现"幽灵运行中"。
        # 取消语义要保留（re-raise），但先尽力把失败态落盘成"取消"。
        result.status = JobStatus.FAILED
        result.step = step
        result.error = "任务被取消（请求中断 / 服务关闭）"
        try:
            store.update(job_id, status=JobStatus.FAILED, step=step, error=result.error)
            store.save_result(job_id, result)
        finally:
            raise
    except Exception as exc:  # noqa: BLE001 —— 边界：任何一步炸都转成分段失败
        result.status = JobStatus.FAILED
        result.step = step
        result.error = f"{type(exc).__name__}: {exc}"
        store.update(job_id, status=JobStatus.FAILED, step=step,
                     error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
    else:
        store.update(job_id, status=JobStatus.DONE, step="done")
        result.status = JobStatus.DONE
        result.step = "done"

    store.save_result(job_id, result)
    return result


def _load_offers(corpus_dir: Path) -> list:
    offers = []
    for f in _CORPUS_FILES:
        p = Path(corpus_dir) / f
        if not p.exists():
            continue
        offers += calc_extract.from_text(p.read_text(encoding="utf-8"), f)
    return offers
