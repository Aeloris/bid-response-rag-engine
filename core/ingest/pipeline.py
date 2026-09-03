# -*- coding: utf-8 -*-
"""入库流水线：语料目录 → 分块 → 向量化 → 写入 VectorStore。

- 幂等：Chunk.chunk_id 由内容哈希生成，重复入库同名文件不产生重复内容点（同 id 覆盖写）；
- 先清空集合再整包重建（当前语料规模小；增量更新留待 Phase 6 的增量入库需求）；
- 返回 IngestReport 供上层展示"入了几个文件 / 几块 / 耗时"。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import Settings
from core.embeddings import get_embedding_provider
from core.ingest.chunker import chunk_file
from core.vector_store import VectorStore

_SUPPORTED = {".md", ".txt", ".markdown"}


@dataclass
class IngestReport:
    files: list[str] = field(default_factory=list)
    total_chunks: int = 0
    elapsed_sec: float = 0.0
    errors: list[str] = field(default_factory=list)


async def ingest_corpus(corpus_dir: str | Path, store: VectorStore, settings: Settings) -> IngestReport:
    """把 corpus_dir 下的 md/txt 全部入库。store 需已按 embedding 维度建好集合。"""
    emb = get_embedding_provider(settings)
    if emb.dimension != store.dimension:
        raise ValueError(
            f"embedding 维度({emb.dimension}) != store 维度({store.dimension})，"
            "collection 需按 embedding.dimension 重建"
        )

    report = IngestReport()
    t0 = time.time()
    dir_path = Path(corpus_dir)
    files = sorted(p for p in dir_path.rglob("*") if p.suffix.lower() in _SUPPORTED)
    if not files:
        raise FileNotFoundError(f"语料目录无受支持文件(md/txt): {dir_path}")

    store.reset()

    for path in files:
        try:
            chunks = chunk_file(path, settings.chunking)
            if not chunks:
                continue
            vectors = await emb.embed([c.text for c in chunks])
            store.upsert_chunks(chunks, vectors)
            report.files.append(path.name)
            report.total_chunks += len(chunks)
        except Exception as exc:  # noqa: BLE001 —— 单文件失败不阻断整包入库，留痕
            report.errors.append(f"{path.name}: {exc}")

    report.elapsed_sec = round(time.time() - t0, 3)
    return report
