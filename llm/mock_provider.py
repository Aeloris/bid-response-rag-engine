# -*- coding: utf-8 -*-
"""Mock LLM Provider：不联网，读 fixtures/llm/<case>.json 返回固定内容。

为什么必须有它：
1. 没配 DASHSCOPE_API_KEY 也能全流程离线跑通（开源可复现的关键）；
2. 测试结果确定、可断言；
3. 到接真实 key 时，只是把 provider 从 mock 切成 dashscope，业务代码零改动。

本阶段(Phase 0)所有 chat 都返回同一份 ping fixture；
后续 Phase 按调用场景传 case（如 generator/answer），实现"按场景给固定返回"。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.settings import Settings


class MockProvider:
    """记录每一次调用（calls），供测试断言；按 case 名加载 fixture。"""

    def __init__(self, settings: Settings, case: str = "ping") -> None:
        self._fixture_dir: Path = settings.fixtures_path / "llm"
        self.case = case
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: type | None = None,
    ) -> Any:
        self.calls.append({"messages": messages, "case": self.case})

        path = self._fixture_dir / f"{self.case}.json"
        if not path.exists():
            raise FileNotFoundError(f"Mock fixture 不存在: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        content = payload.get("content", payload) if isinstance(payload, dict) else payload

        if schema is not None:
            # 若给定了 schema，就按 schema 校验返回（本阶段 fixture 非 dict 时跳过校验）
            return schema.model_validate(content) if isinstance(content, dict) else content
        return content

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MockProvider case={self.case} calls={len(self.calls)}>"
