# -*- coding: utf-8 -*-
"""模型层：全项目 LLM 调用的唯一入口。

对外只暴露 get_provider() 工厂：
    from config.settings import get_settings
    from llm import get_provider
    llm = get_provider(get_settings())
    out = await llm.chat([{"role": "user", "content": "ping"}])   # -> "pong"

换模型（mock ↔ dashscope）只改 config.yaml 的 llm.provider，业务代码不动。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import LLMProvider
from .dashscope_provider import DashScopeProvider
from .mock_provider import MockProvider

if TYPE_CHECKING:
    from config.settings import Settings


def get_provider(settings: "Settings") -> LLMProvider:
    """按配置返回 LLM 实现：dashscope -> 真实；否则默认 mock。"""
    if settings.llm.provider == "dashscope":
        return DashScopeProvider(settings)
    return MockProvider(settings)


__all__ = ["LLMProvider", "get_provider"]
