# -*- coding: utf-8 -*-
"""FastAPI 应用入口（服务层）。

Phase 0：只有 /health 与根路由，证明服务与配置能起；
Phase 6：在这里挂载 /parse /pipeline 等业务路由。

启动即做配置校验：导入时调用 get_settings()，若 provider=dashscope 而无 key，
会立刻抛 ValueError（fail fast），而不是等到用户请求才炸。
"""
from __future__ import annotations

from fastapi import FastAPI

from config.settings import get_settings

settings = get_settings()  # 启动即校验配置

app = FastAPI(
    title=settings.app.name,
    version="0.1.0",
    description="投标应答 Agent：逐评分点检索取证→生成应答→旧数据自检→带页码溯源输出",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """存活探针：容器编排 / CI / 前端都会先打这里。"""
    return {"status": "ok"}


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    """根路由：一眼看清当前运行态（默认 mock = 没 key 也能跑）。"""
    return {
        "name": settings.app.name,
        "status": "ok",
        "llm_provider": settings.llm.provider,
        "interactive_docs": "/docs",
    }
