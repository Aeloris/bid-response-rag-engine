# -*- coding: utf-8 -*-
"""配置层测试：默认 mock；dashscope 无 key 必须启动即报错（fail fast）。"""
from __future__ import annotations

import pytest

from config.settings import Settings


def test_default_provider_is_mock(settings) -> None:
    assert settings.llm.provider == "mock"


def test_dashscope_without_key_fails_fast(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        Settings(llm={"provider": "dashscope"})
