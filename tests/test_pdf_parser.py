"""P02-10 PDF 解析器主路径测试（PyMuPDF，小型内存 fixture）。"""

from __future__ import annotations

import fitz
import pytest

from invest_research.tools.pdf_parser import PDFParseError, parse_pdf


def _make_pdf(text: str) -> bytes:
    """用 PyMuPDF 在内存中生成 1 页文本 PDF 字节。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_parses_text_with_page_number() -> None:
    """解析成功：返回文本与页码（1-based）。"""
    parsed = parse_pdf(_make_pdf("Hello SEC"))

    assert len(parsed.blocks) == 1
    block = parsed.blocks[0]
    assert block.text == "Hello SEC"
    assert block.page_number == 1


def test_invalid_pdf_raises() -> None:
    """无效 PDF 字节 → PDFParseError。"""
    with pytest.raises(PDFParseError):
        parse_pdf(b"not a pdf")


def test_blank_pdf_yields_no_blocks() -> None:
    """空白页（无文字）→ 无 blocks，不报错；PyMuPDF 不允许零页 PDF。"""
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    assert parse_pdf(data).blocks == ()
