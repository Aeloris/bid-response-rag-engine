# -*- coding: utf-8 -*-
"""规则层测试：loader 读 PDF、规则锚点命中合成样例的全部目标栏目。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.parser.loader import PDFLoader
from core.parser.rules import find_sections, is_chapter_heading

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tender_sample.pdf"


@pytest.fixture(scope="module")
def pages():
    assert FIXTURE.exists(), "请先运行 uv run python scripts/make_tender_fixture.py"
    return PDFLoader().load(FIXTURE)


def test_pdf_has_text_layer(pages) -> None:
    assert len(pages) >= 1
    # 抽样断言：文本层应能读出中文内容，而不是空页
    joined = "\n".join(p.text for p in pages)
    assert "招标" in joined or "智慧园区" in joined
    assert "★" in joined


def test_chapter_heading_detection() -> None:
    assert is_chapter_heading("第一章 项目概况")
    assert is_chapter_heading("第3章 评标办法")
    assert not is_chapter_heading("SP-01 技术方案 10分 见上文")  # 评分点行不是章节标题
    assert not is_chapter_heading("这里是普通正文段落内容...")


def test_sections_all_six_types_found(pages) -> None:
    sections = find_sections(pages)
    assert set(sections) == {
        "score_points",
        "star_clauses",
        "tech_params",
        "eligibility",
        "waste_bid",
        "timeline",
    }


def test_star_and_waste_sections_isolated(pages) -> None:
    """★ 条款章与废标章互不污染（标题都含'废标/实质性'，需按栏目归属切分正确）。"""
    sections = find_sections(pages)
    assert "一票否决" in sections["star_clauses"].heading
    assert "废标" in sections["waste_bid"].heading
    # ★ 条款正文出现 ★ 前缀，废标章正文出现 (一) 项列举
    assert sections["star_clauses"].text.lstrip().startswith("★") or "★5.1" in sections["star_clauses"].text
    assert "质保" in sections["star_clauses"].text
