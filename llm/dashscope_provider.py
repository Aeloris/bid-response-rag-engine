# -*- coding: utf-8 -*-
"""DashScope LLM Provider（骨架）。

Phase 0 只搭接口，不实现真实调用。Phase 1 接入 key 后在这里实现：
- 走 DashScope 的 OpenAI 兼容端点 base_url（config.yaml llm.base_url）；
- 请求带 timeout 与重试（对接 tenacity）；
- schema 非空时用 function calling / response_format 强制结构化输出并校验。
"""
from __future__ import annotations

from typing import Any

from config.settings import Settings


class DashScopeProvider:
    """真实模型实现。Phase 1 完成。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # 配置层已保证：provider=dashscope 时 api_key 一定存在（fail fast）
        self._api_key: str = settings.llm.api_key  # type: ignore[assignment]

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: type | None = None,
    ) -> Any:
        raise NotImplementedError(
            "DashScopeProvider 尚未实现：Phase 1 接入 DashScope 后可用。"
        )
