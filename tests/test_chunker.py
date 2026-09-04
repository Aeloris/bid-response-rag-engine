# -*- coding: utf-8 -*-
"""切块器测试：标题感知边界、章节路径、超长细分 + overlap。"""
from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import ChunkingConfig
from core.ingest.chunker import chunk_file, chunk_markdown

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
CFG = ChunkingConfig()


def test_chunks_carry_heading_path_and_source() -> None:
    text = "# 产品\n\n## 摄像机\n\n分辨率400万像素。\n\n### 智能\n\n支持人脸抓拍。\n\n## NVR\n\n接入32路。"
    chunks = chunk_markdown(text, "p.md", CFG)
    assert chunks, "应有块产出"
    assert all(c.source == "p.md" for c in chunks)

    cam = next(c for c in chunks if "400万" in c.text)
    assert "摄像机" in cam.heading_path
    nvr = next(c for c in chunks if "32路" in c.text)
    assert "NVR" in nvr.heading_path
    assert "摄像机" not in nvr.heading_path  # 兄弟章节不串


def test_long_section_is_split_with_overlap() -> None:
    # 一个标题下塞超长正文 → 切成多块且相邻块有重叠文字
    body = ("甲乙丙丁戊己庚辛壬癸" * 200)  # 2000 字
    text = f"# 大章节\n\n{body}"
    cfg = ChunkingConfig(max_chars=300, overlap_chars=50)
    chunks = chunk_markdown(text, "long.md", cfg)
    assert len(chunks) >= 2
    # overlap 生效：前一块的尾部应出现在后一块的开头附近
    tail = chunks[0].text[-30:]
    assert any(tail in c.text for c in chunks[1:])


def test_chunk_id_stable_for_same_content() -> None:
    a = chunk_markdown("# H\n\n正文内容块。", "x.md", CFG)[0]
    b = chunk_markdown("# H\n\n正文内容块。", "x.md", CFG)[0]
    assert a.chunk_id == b.chunk_id


def test_corpus_files_chunk_cleanly() -> None:
    for md in sorted(CORPUS.glob("*.md")):
        chunks = chunk_file(md, CFG)
        assert chunks, md.name
        # 文本块不应被标题行污染成空
        assert all(c.text.strip() for c in chunks)
        # chunk 内容总长应接近源文本（标题不进文本块，故放宽到 0.6 防误报；分块重叠只多不少）
        joined = "".join(c.text for c in chunks)
        src = md.read_text(encoding="utf-8")
        assert len(joined) >= len(src) * 0.6, md.name


def test_markdown_extension_uses_heading_chunks(tmp_path: Path) -> None:
    """F14 回归：.markdown 扩展名应走标题感知切块（与 .md 同待遇），不能退化成纯文本。"""
    p = tmp_path / "guide.markdown"
    p.write_text("# 售后\n\n整机质保 3 年。\n\n# 摄像机\n\n分辨率 400 万像素。", encoding="utf-8")
    chunks = chunk_file(p, CFG)
    assert len(chunks) >= 2, "两章应各成一块"
    cam = next(c for c in chunks if "万像素" in c.text)
    assert "摄像机" in cam.heading and "摄像机" in cam.heading_path
    # 标题感知下两章各成一块（若退化成纯文本整篇单块则只有 1 块）
    assert any("质保" in c.text for c in chunks)


def test_empty_and_whitespace_files_produce_no_chunks(tmp_path: Path) -> None:
    """F15 回归：空/纯空白 .txt（及 .md）不得产出空块（否则会嵌入空向量污染检索）。"""
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    assert chunk_file(empty, CFG) == []

    ws = tmp_path / "ws.txt"
    ws.write_text("   \n\t  \n", encoding="utf-8")
    assert chunk_file(ws, CFG) == []

    em = tmp_path / "blank.md"
    em.write_text("", encoding="utf-8")
    assert chunk_file(em, CFG) == []
