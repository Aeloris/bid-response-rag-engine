# -*- coding: utf-8 -*-
"""端到端：入库样例语料 → 混合检索能召回正确素材（Mock Embedding，离线确定）。

离线命中靠 BM25 + 词法重排撑住（伪向量无语义），这正说明"混合检索"里词法通道的必要性。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.embeddings import get_embedding_provider
from core.ingest import ingest_corpus
from core.retriever import Retriever
from core.vector_store import VectorStore

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "fixtures" / "corpus"


def _fresh_store_and_retriever(settings):
    store = VectorStore(
        collection=settings.vector_db.collection,
        dimension=settings.embedding.dimension,
        path=":memory:",  # 测试内存库，隔离不污染 ./data
    )
    retriever = Retriever(settings, store)
    return store, retriever


def test_ingest_corpus_and_retrieve_camera_material(settings) -> None:
    async def run():
        store, retriever = _fresh_store_and_retriever(settings)
        report = await ingest_corpus(CORPUS, store, settings)
        return store, retriever, report

    store, retriever, report = asyncio.run(run())
    assert report.errors == []
    assert report.total_chunks > 0
    assert store.count() == report.total_chunks
    assert "cases.md" in report.files and "product-guide.md" in report.files

    results = asyncio.run(
        retriever.retrieve("高清网络摄像机 400万像素 人脸抓拍", top_k=3)
    )
    assert results, "应召回引用块"
    top = results[0]
    # 词法通道 + 重排应把 400W 摄像机块顶到最前（伪向量不背这锅）
    assert top.source == "product-guide.md"
    assert "400" in top.text
    assert all("dense_rank" in r.ranks and "bm25_rank" in r.ranks for r in results)


def test_retrieve_service_warranty_material(settings) -> None:
    async def run():
        store, retriever = _fresh_store_and_retriever(settings)
        await ingest_corpus(CORPUS, store, settings)
        return retriever

    retriever = asyncio.run(run())
    results = asyncio.run(retriever.retrieve("质保期三年 7×24小时 重大故障2小时到场"))
    assert results
    top = results[0]
    assert top.source == "qualifications-and-service.md"
    assert "质保" in top.text
