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


def test_embedding_dashscope_key_check_uses_llm_api_key_env(monkeypatch) -> None:
    """F11 回归：Embedding/Rerank 的 fail-fast 须按 llm.api_key_env 指定的变量查 key，
    不能写死 DASHSCOPE_API_KEY（否则改了变量名就漏检）。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        Settings(embedding={"provider": "dashscope"})

    monkeypatch.setenv("MY_DASHSCOPE_KEY", "sk-test")
    s = Settings(llm={"api_key_env": "MY_DASHSCOPE_KEY"},
                 embedding={"provider": "dashscope"}, rerank={"provider": "dashscope"})
    assert s.embedding.provider == "dashscope" and s.rerank.provider == "dashscope"
