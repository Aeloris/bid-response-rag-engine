# -*- coding: utf-8 -*-
"""服务层冒烟测试：应用能起、/health 通、默认 mock 运行态。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_reports_mock_provider_by_default() -> None:
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "mock"  # 默认离线可跑
