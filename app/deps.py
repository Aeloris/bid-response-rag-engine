# -*- coding: utf-8 -*-
"""FastAPI 依赖：配置与 LLM 的依赖注入。

- get_settings：进程级单例（config.get_settings 本身 lru_cache），provider=mock → 离线可跑；
- get_llm：由 llm.get_provider 工厂按 config.yaml 决定 mock/dashscope，业务代码零改动。
两者以 Depends 组合，测试可覆盖替换（TestClient dependency_overrides）。
"""
from __future__ import annotations

from fastapi import Depends

from config.settings import Settings, get_settings as _load_settings
from llm import get_provider


def get_settings() -> Settings:
    return _load_settings()


def get_llm(settings: Settings = Depends(get_settings)) -> object:
    return get_provider(settings)


def get_job_store(settings: Settings = Depends(get_settings)):
    """任务落盘工作区（文件即状态，进程内无共享内存 → 单 worker 可复用实例）。"""
    from app.jobs import JobStore

    return JobStore(settings.data_path / "jobs")
