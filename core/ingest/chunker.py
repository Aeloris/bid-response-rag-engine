# -*- coding: utf-8 -*-
"""标题感知切块器：把一份 Markdown 语料切成语义完整的 Chunk。

为什么用"标题感知"而不是按固定字符数硬切？
1. 硬切会把"分辨率≥400万像素"或一张参数表从中间劈开 → 语义残缺，检索命中后也无法应答；
2. 标题是天然语义边界：`# 产品线` 下的内容自成一体。按标题切，块自带"章节路径"，
   命中后能回答"这段来自产品手册的哪一节"→ 引用溯源。

策略（见 config.chunking）：
- 按 Markdown 标题层级(#/##/###)建立边界；两个标题之间是一段候选；
- 超长候选再按 max_chars 细分，并在块之间保留 overlap_chars 重叠，补偿切断处的上下文丢失；
- 无标题的零散文档退化为按字符切 + overlap。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import ChunkingConfig

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    """入库的最小检索单元。"""

    chunk_id: str            # 内容哈希，内容不变则 id 不变（便于幂等重建）
    text: str
    source: str              # 来源文件名
    heading: str = ""        # 最近一级标题，给人看的短出处
    heading_path: list[str] = field(default_factory=list)  # 章节路径，引用溯源用

    @staticmethod
    def make_id(text: str, source: str) -> str:
        return hashlib.sha1(f"{source}\x00{text}".encode("utf-8")).hexdigest()[:16]


def _split_long(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """把一段超长文本按近似字符位置细切，切点尽量落在换行处。"""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    rest = text
    while len(rest) > max_chars:
        cut = rest.rfind("\n", max_chars // 2, max_chars)
        if cut == -1:
            cut = max_chars
        parts.append(rest[:cut])
        rest = rest[max(0, cut - overlap_chars):]
    parts.append(rest)
    return parts


def chunk_markdown(text: str, source: str, cfg: ChunkingConfig) -> list[Chunk]:
    """对单份 Markdown 语料做标题感知切块。

    启发式：
    - 逐行扫描，维护标题栈（更低级别标题结束高层标题的当前段）；
    - 标题行本身不开块（块正文从标题后的内容开始），块的 heading_path 记录到该标题；
    - 遇到下一个同级或更高级标题 → 结束当前块；更深层标题只加深 path。
    """
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []   # (级别, 标题文本)
    buf: list[str] = []
    buf_heading = ""
    buf_path: list[str] = []

    def flush() -> None:
        nonlocal buf, buf_heading, buf_path
        body = "\n".join(buf).strip()
        if body:
            for piece in _split_long(body, cfg.max_chars, cfg.overlap_chars):
                head = " / ".join([h for _, h in stack] + ([buf_heading] if buf_heading else []))
                chunks.append(
                    Chunk(
                        chunk_id=Chunk.make_id(piece, source),
                        text=piece,
                        source=source,
                        heading=buf_heading or (stack[-1][1] if stack else ""),
                        heading_path=[h for _, h in stack] + ([buf_heading] if buf_heading else []),
                    )
                )
        buf, buf_heading, buf_path = [], "", []

    for raw in text.splitlines():
        m = _HEADING_RE.match(raw)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            buf_path = [h for _, h in stack]
            continue
        buf.append(raw)
    flush()
    return chunks


def chunk_file(path: str | Path, cfg: ChunkingConfig) -> list[Chunk]:
    """切一个文件（按扩展名选择解析，当前支持 .md/.txt）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".md":
        return chunk_markdown(text, p.name, cfg)
    # 纯文本：退化为无标题字符切 + overlap
    out: list[Chunk] = []
    for piece in _split_long(text, cfg.max_chars, cfg.overlap_chars):
        out.append(Chunk(chunk_id=Chunk.make_id(piece, p.name), text=piece, source=p.name))
    return out
