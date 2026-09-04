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


class EmbeddingConfig(BaseModel):
    provider: str = "mock"  # mock | dashscope
    model: str = "text-embedding-v3"
    dimension: int = 1024
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class RerankConfig(BaseModel):
    provider: str = "mock"  # mock | dashscope
    model: str = "gte-rerank-v2"


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


class GeneratorConfig(BaseModel):
    max_contexts: int = 4  # 每个评分点最多给生成器的引用块数
    max_chars_per_context: int = 1500  # 单引用块截断长度（控 token）


class ParserConfig(BaseModel):
    rule_anchors: list[str] = Field(
        default_factory=lambda: ["评标办法", "技术规格", "资格要求", "废标", "★"]
    )
    llm_batch_size: int = 10


class QAConfig(BaseModel):
    """Phase 5 自检质检开关。"""

    consistency_tol: float = 1e-9  # 数值自洽容差
    judge_all: bool = False  # False=只送"有引用且未被代码 BLOCK"的点给 Judge（省钱、稳）
    max_attempts: int = 1  # 改写闭环限次（含首轮，1=只改写一轮）
    min_citations_for_judge: int = 1  # 无引用的点不进 Judge（宁缺毋滥，直接需人工）


class EvalThresholds(BaseModel):
    """评测门禁（CI 基线，基于 mock 确定性实测回填；低于则 run.py exit 1）。

    诚实约定：这些阈值是「离线 mock 基线」的门禁，不是"我多好"的宣称——
    防止改引擎后偷偷回退。跑真实模型版（provider=dashscope 且 LLM/Embedding 已接入）
    时门禁关闭（只提示），因为真模型版基线需另标。
    """

    parse_point_f1: float = 0.9          # 解析评分点 F1 下限（实测 1.0）
    retrieval_hybrid_recall_at_k: float = 0.6   # 混合检索 Recall@5 下限（实测 0.733）
    retrieval_hybrid_mrr_at_k: float = 0.6      # 混合检索 MRR@5 下限（实测 0.700）
    qa_bad_detection_rate: float = 1.0   # 坏例检出率下限（3/3 代码判确定性）
    qa_good_fp_rate_max: float = 0.0     # 好例误报率上限（合规草稿不得误拦）


class EvalConfig(BaseModel):
    output_dir: str = "./data/eval"  # eval_report.json/.md 输出目录（gitignore 覆盖）
    top_k: int = 5
    thresholds: EvalThresholds = Field(default_factory=EvalThresholds)


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    vector_db: VectorDBConfig = Field(default_factory=VectorDBConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    generator: GeneratorConfig = Field(default_factory=GeneratorConfig)
    parser: ParserConfig = Field(default_factory=ParserConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "Settings":
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(**data)

    @model_validator(mode="after")
    def _fail_fast_when_any_dashscope_without_key(self) -> "Settings":
        """Embedding/Rerank 与 LLM 共用同一鉴权密钥（DashScope 统一）→ 一律按 llm.api_key_env
        指定的环境变量检查，而不是写死 DASHSCOPE_API_KEY（否则用户改了 api_key_env 会漏检）。
        """
        need_key = os.getenv(self.llm.api_key_env)
        for name, prov in (("embedding", self.embedding.provider), ("rerank", self.rerank.provider)):
            if prov == "dashscope" and not need_key:
                raise ValueError(
                    f"{name}.provider=dashscope 但环境变量 {self.llm.api_key_env} 未设置；"
                    f"离线可把 config.yaml 的 {name}.provider 保持为 mock。"
                )
        return self

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
