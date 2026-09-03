# -*- coding: utf-8 -*-
"""Phase 6 API 层测试：接口契约、任务状态机、错误分段。

离线约定：默认 config.yaml provider=mock → 无 key 全程可跑。
POST /tasks 会走完 parse→…→qa 全流水线并落盘 data/jobs/{id}/（gitignored 运行产物）。
断言不写死具体数字（依赖语料/样例会漂），只锁契约形状与"样例合规"这一类结论。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = REPO_ROOT / "fixtures" / "tender_sample.pdf"
PDF_CT = "application/pdf"


def _post_sample(client: TestClient, url: str):
    """上传真实样例 PDF 到指定端点。"""
    with SAMPLE_PDF.open("rb") as f:
        return client.post(url, files={"file": ("tender_sample.pdf", f, PDF_CT)})


# ------------------------------------------------------------ /tenders/parse
def test_parse_sample_pdf_lists_score_points() -> None:
    with TestClient(app) as client:
        resp = _post_sample(client, "/tenders/parse")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["source_file"].endswith(".pdf")
    assert len(body["score_points"]) >= 5          # 评分点速览足够多
    assert body["star_count"] >= 1                 # 有 ★ 关键项标记
    ids = [p["id"] for p in body["score_points"]]
    assert len(ids) == len(set(ids))               # id 唯一，可作回链锚点
    # 每个评分点都给出应答要求 + 证据类型，前端才能先渲染清单
    for p in body["score_points"]:
        assert "content" in p
        assert "evidence_type" in p


# ------------------------------------------------------------ 任务状态机
def test_create_task_runs_full_pipeline_result_has_all_phases() -> None:
    with TestClient(app) as client:
        created = _post_sample(client, "/tasks")
        assert created.status_code == 202          # 建任务即接受
        state = created.json()
        job_id = state["job_id"]
        assert state["status"] == "done"           # v1 同步执行：返回即终态

        # 轮询状态端点
        got = client.get(f"/tasks/{job_id}")
        assert got.status_code == 200
        assert got.json()["job_id"] == job_id
        assert got.json()["status"] == "done"

        # 拉整条产物
        res = client.get(f"/tasks/{job_id}/result")
        assert res.status_code == 200

    body = res.json()
    assert body["job_id"] == job_id
    assert body["status"] == "done"
    assert body["tender_title"]
    assert len(body["score_points"]) >= 5
    # 五引擎产物三段都在：gen / calc / qa
    assert body["gen"]["total"] >= 1 and body["gen"]["answered"] >= 1
    assert body["calc"]["total"] >= 1
    assert body["qa"]["escalation_required"] is False   # 样例合规：无废标级 BLOCK
    # computed_field 进序列化：待补材料可直接取
    assert isinstance(body["needs_material"], list)
    assert body["escalation_required"] is False


# ------------------------------------------------------------ 错误分段（4xx 而非 500）
def test_reject_non_pdf_returns_4xx() -> None:
    fake = b"# definitely not a pdf, no magic header"
    with TestClient(app) as client:
        for url in ("/tenders/parse", "/tasks"):
            resp = client.post(url, files={"file": ("evil.pdf", fake, "text/plain")})
            assert resp.status_code == 400
            assert resp.status_code < 500           # 用户输入错 → 4xx，不炸 500


def test_unknown_task_is_404() -> None:
    with TestClient(app) as client:
        assert client.get("/tasks/nope").status_code == 404
        assert client.get("/tasks/nope/result").status_code == 404
