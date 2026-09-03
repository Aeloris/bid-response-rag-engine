# -*- coding: utf-8 -*-
"""Mock Provider 测试：不联网、返回固定 fixture、且记录调用。"""
from __future__ import annotations

import asyncio

from llm import get_provider


def test_mock_ping_returns_pong_and_records_call(settings) -> None:
    provider = get_provider(settings)  # 默认 mock

    out = asyncio.run(provider.chat([{"role": "user", "content": "ping"}]))

    assert out == "pong"
    assert len(provider.calls) == 1
    assert provider.calls[0]["case"] == "ping"
