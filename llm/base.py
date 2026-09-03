# -*- coding: utf-8 -*-
"""LLM 调用契约（抽象层）。

全项目规则：业务代码只依赖本文件定义的接口，不直接接触任何模型 SDK。
- mock_provider：读 fixtures 固定返回，离线可测（默认）；
- dashscope_provider：真实实现，Phase 1 接 key 后启用。

调用契约：
    async def chat(messages, *, schema=None) -> Any
  - messages: OpenAI 风格消息列表 [{"role","content"}, ...]
  - schema:   可选的 pydantic 模型类型。给定则返回已通过 schema 校验的对象；
              否则返回纯文本 str。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # 仅类型标注用，避免循环导入
    from pydantic import BaseModel


@runtime_checkable
class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: type["BaseModel"] | None = None,
    ) -> Any:
        """发送消息并返回内容。实现方必须可重入、可在请求级取消。"""
        ...
