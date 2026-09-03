# -*- coding: utf-8 -*-
"""DashScopeEmbedding：真实向量化（text-embedding-v3，OpenAI 兼容 /embeddings）。

与 llm/dashscope_provider.py 同源：httpx + DashScope compatible-mode。
仅当 config.embedding.provider=dashscope 且配了 key 才会被实例化（fail-fast 已在配置层拦）。
"""
from __future__ import annotations

import httpx

from config.settings import Settings


class DashScopeEmbedding:
    def __init__(self, settings: Settings) -> None:
        self.dimension = settings.embedding.dimension
        self._cfg = settings.embedding
        self._key = settings.llm.api_key  # 与 LLM 共用 DASHSCOPE_API_KEY
        self._timeout = httpx.Timeout(settings.llm.timeout_sec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._key:
            raise RuntimeError("DashScopeEmbedding 缺 key（配置层应已 fail-fast，属异常路径）")
        url = f"{self._cfg.base_url.rstrip('/')}/embeddings"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self._cfg.model,
                    "input": texts,
                    "dimensions": self._cfg.dimension,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data["data"]]
