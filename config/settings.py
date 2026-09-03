# -*- coding: utf-8 -*-
"""配置加载：config/config.yaml（业务参数） + .env / 环境变量（密钥）。

用法：
    from config.settings import get_settings
    s = get_settings()          # 进程内只加载一次（lru_cache）
    s.llm.provider             # -> "mock"
    s.retrieval.rrf_k          # -> 60

设计要点（防后期返工）：
- 所有未来阶段会用到的参数已在 config.yaml 占位，这里定义强类型模型，
  拼错字段会在启动时立刻报错，而不是运行到一半才发现。
- 密钥（DASHSCOPE_API_KEY）只从环境变量 / .env 读取，绝不出现在 yaml。
- llm.provider=dashscope 但缺少 key 时，启动即抛清晰错误（fail fast）。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

# 仓库根目录 = config/ 的上一级；所有相对路径都从这里解析
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"

# 把仓库根 .env 里的键注入 os.environ（若存在）；不存在则静默跳过
load_dotenv(REPO_ROOT / ".env")


class AppConfig(BaseModel):
    name: str = "bid-response-agent"
    log_level: str = "INFO"
    data_dir: str = "./data"
    fixtures_dir: str = "./fixtures"


class LLMConfig(BaseModel):
    provider: str = "mock"  # mock | dashscope
    model: str = "qwen-plus"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_sec: float = 60.0
    temperature: float = 0.2
    max_retries: int = 3
    api_key_env: str = "DASHSCOPE_API_KEY"

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env)

    @model_validator(mode="after")
    def _fail_fast_when_dashscope_without_key(self) -> "LLMConfig":
        if self.provider == "dashscope" and not self.api_key:
            raise ValueError(
                f"llm.provider=dashscope 但环境变量 {self.api_key_env} 未设置。\n"
                "解决办法：复制 .env.example 为 .env 并填入真实 key；"
                "或把 config.yaml 的 llm.provider 保持为 mock 离线运行。"
            )
        return self


def _dashscope_key() -> str | None:
    """Embedding/Rerank 复用与 LLM 同一个密钥环境变量（DashScope 统一鉴权）。"""
    return os.getenv("DASHSCOPE_API_KEY")


class EmbeddingConfig(BaseModel):
    provider: str = "mock"  # mock | dashscope
    model: str = "text-embedding-v3"
    dimension: int = 1024
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @model_validator(mode="after")
    def _fail_fast_when_dashscope_without_key(self) -> "EmbeddingConfig":
        if self.provider == "dashscope" and not _dashscope_key():
            raise ValueError(
                "embedding.provider=dashscope 但 DASHSCOPE_API_KEY 未设置；"
                "离线可把 config.yaml 的 embedding.provider 保持为 mock。"
            )
        return self


class RerankConfig(BaseModel):
    provider: str = "mock"  # mock | dashscope
    model: str = "gte-rerank-v2"

    @model_validator(mode="after")
    def _fail_fast_when_dashscope_without_key(self) -> "RerankConfig":
        if self.provider == "dashscope" and not _dashscope_key():
            raise ValueError(
                "rerank.provider=dashscope 但 DASHSCOPE_API_KEY 未设置；"
                "离线可把 config.yaml 的 rerank.provider 保持为 mock。"
            )
        return self


class VectorDBConfig(BaseModel):
    provider: str = "qdrant_local"  # qdrant_local | qdrant_server
    path: str = "./data/qdrant"
    collection: str = "tender_corpus"


class ChunkingConfig(BaseModel):
    mode: str = "heading_aware"
    max_chars: int = 1200
    overlap_chars: int = 100


class RetrievalConfig(BaseModel):
    dense_top_k: int = 20
    bm25_top_k: int = 20
    rrf_k: int = 60
    rerank_top_n: int = 6


class ParserConfig(BaseModel):
    rule_anchors: list[str] = Field(
        default_factory=lambda: ["评标办法", "技术规格", "资格要求", "废标", "★"]
    )
    llm_batch_size: int = 10


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    vector_db: VectorDBConfig = Field(default_factory=VectorDBConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    parser: ParserConfig = Field(default_factory=ParserConfig)

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "Settings":
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(**data)

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def data_path(self) -> Path:
        """运行产物目录（自动创建）。"""
        p = self.repo_root / self.app.data_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def fixtures_path(self) -> Path:
        """fixtures 目录（不存在则创建）。"""
        p = self.repo_root / self.app.fixtures_dir
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings(path: str | Path | None = None) -> Settings:
    """进程内共享同一份配置；测试里想用别的 yaml 可传 path。"""
    return Settings.from_yaml(path)
