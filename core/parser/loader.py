# -*- coding: utf-8 -*-
"""版面还原层：把招标书 PDF 读成一页页可检索的文本。

设计要点：
- 用 PyMuPDF(fitz) 做纯本地、快速文本抽取，不上传文件，符合投标敏感数据诉求；
- 返回 Page 列表（页码 + 全文），保留 page_no 供后续"★在第几页"溯源用；
- 仅负责"读取与分页"，不负责语义——语义拆分交给 rules.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # PyMuPDF ≥1.24 推荐顶层模块名 pymupdf
    import pymupdf as fitz
except ImportError:  # 老版本仍提供 fitz 别名
    import fitz


@dataclass
class Page:
    """PDF 的一页。text 为该页全部可见文本（含换行符）。"""

    page_no: int
    text: str

    def lines(self) -> list[str]:
        """按行拆分（保留原始行尾判定）。"""
        return self.text.splitlines()


@dataclass
class PDFLoader:
    """加载 PDF 并分页。不处理扫描件 OCR（见 docs/parser.md 限制说明）。"""

    def load(self, pdf_path: str | Path) -> list[Page]:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到招标书 PDF: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"仅支持 PDF，收到: {path.suffix or '(无扩展名)'}")

        pages: list[Page] = []
        with fitz.open(path) as doc:
            if doc.page_count == 0:
                raise ValueError(f"PDF 无页面: {path}")
            for page_no in range(doc.page_count):
                text = doc.load_page(page_no).get_text("text")
                pages.append(Page(page_no=page_no + 1, text=text))
        return pages

    @staticmethod
    def full_text(pages: list[Page]) -> str:
        """整份 PDF 的拼接文本（顺序保留，页间加换行）。"""
        return "\n".join(p.text for p in pages)
