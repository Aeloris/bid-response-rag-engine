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


def test_same_type_across_multiple_chapters_accumulates() -> None:
    """F16 回归：同一栏目在多章出现应**累计**（文档顺序），不再"后者覆盖前者"丢前半章。"""
    from core.parser.loader import Page

    text = (
        "第一章 评标办法\n评分点A：质保期不少于36个月\n"
        "第二章 项目概况\n无关内容不应进评标栏目\n"
        "第三章 评分办法\n评分点B：故障响应时间\n"
    )
    sections = find_sections([Page(page_no=1, text=text)])
    sc = sections["score_points"]
    assert "评分点A" in sc.text and "评分点B" in sc.text  # 两章都留
    assert "无关内容不应进评标栏目" not in sc.text         # 无栏目章节正文不串入
    # 首次出现那章的标题记为 span.heading
    assert "第一章" in sc.heading


def test_combined_heading_fans_out_to_every_matched_type() -> None:
    """F13 回归：一"章"同时命中多类型（评标办法及废标条款）→ 正文馈给**每个**命中类型，
    不能只喂优先级最高的类型、让其余类型拿到空 span（内容静默丢失）。"""
    from core.parser.loader import Page

    text = (
        "第四章 评标办法及废标条款\n"
        "评标要点：逐项打分，满足★为通过\n"
        "下列情形之一即构成废标：(一)未实质响应招标文件。\n"
        "第五章 时间安排\n投标截止时间以公告为准\n"
    )
    sections = find_sections([Page(page_no=1, text=text)])
    assert "评标要点" in sections["score_points"].text
    assert "(一)未实质响应" in sections["waste_bid"].text     # 不再空 span
    # 后一无关章节的正文不串进这两个栏目
    assert "投标截止时间" not in sections["waste_bid"].text
    assert "投标截止时间" not in sections["score_points"].text
    assert "第五章" in sections["timeline"].heading
    assert "投标截止时间" in sections["timeline"].text
