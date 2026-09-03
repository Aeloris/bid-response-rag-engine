# -*- coding: utf-8 -*-
"""Mock LLM Provider：不联网，读 fixtures/llm/<case>.json 返回固定内容。

为什么必须有它：
1. 没配 DASHSCOPE_API_KEY 也能全流程离线跑通（开源可复现的关键）；
2. 测试结果确定、可断言；
3. 到接真实 key 时，只是把 provider 从 mock 切成 dashscope，业务代码零改动。

fixture 选择规则：调用方传了 `schema` → 读 `fixtures/llm/<Schema名>.json`（如 ExtractionResult.json）；
未传 schema → 按构造时给的 case（如 ping）读同名 fixture。这样"换真实模型/换离线结果"都只动 fixture。
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
        # 传了 schema → fixture 名取 schema 类名（如 ExtractionResult.json），
        # 便于"同一管道换真实模型/换 mock 结果"时路径稳定；否则回退 case。
        name = schema.__name__ if schema is not None else self.case
        self.calls.append(
            {
                "messages": messages,
                "case": self.case,
                "fixture": name,
                "schema": schema.__name__ if schema is not None else None,
            }
        )

        path = self._fixture_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Mock fixture 不存在: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        content = payload.get("content", payload) if isinstance(payload, dict) else payload

        if schema is not None:
            # fixture 文件内容即目标对象（或其键），交给 schema 校验，非法内容立刻暴露
            return schema.model_validate(content)
        return content

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MockProvider case={self.case} calls={len(self.calls)}>"
