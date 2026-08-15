"""P06-02 Markdown→PDF 渲染测试（离线，PyMuPDF 内置字体，无外部依赖）。

验证目标（docs/05 P06-02）：
- 中文内容可渲染且可提取（内置 CJK 字体，无乱码/豆腐块）；
- [text](url) 生成可点击 link annotation；
- 长内容自动分页，页脚带页码；
- 基础 Markdown 子集（标题/列表/引用/分隔线/表格）均可渲染；
- 空输入报错；输出确定性（同输入同页数与同文本）。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from invest_research.reporting.pdf import MarkdownPdfRenderer, render_page_png

CJK_SAMPLE = "苹果公司（Apple Inc.）投资研究报告"
URL_SAMPLE = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000123/aapl-20250927.htm"


def _sample_markdown() -> str:
    return (
        "# 苹果公司（Apple Inc.）投资研究报告\n\n"
        "> 本报告由 invest-research 自动化生成，仅供信息整理与技术演示。\n\n"
        "## 执行摘要\n\n"
        "苹果公司是一家美国上市公司，股票代码 AAPL，CIK 为 0000320193。\n\n"
        "## 关键指标表\n\n"
        "| 指标 | 数值 | 单位 |\n"
        "| :--- | :--- | :--- |\n"
        "| 营收 | 391,035 | 百万美元 |\n\n"
        "## 来源清单\n\n"
        "- 年度报告：" + "[SEC 10-K 申报](" + URL_SAMPLE + ")\n"
        "- 财务事实：来自公司披露的 XBRL 数据\n\n"
        "1. 第一项风险\n"
        "2. 第二项风险\n\n"
        "---\n\n"
        "非投资建议声明：本报告不构成任何投资建议。"
    )


def test_render_creates_pdf_with_cjk_text(tmp_path: Path) -> None:
    out = tmp_path / "report.pdf"
    MarkdownPdfRenderer().render(_sample_markdown(), out)
    assert out.exists()
    assert out.stat().st_size > 500

    doc = pymupdf.open(out)
    try:
        assert len(doc) >= 1
        text = "\n".join(page.get_text() for page in doc)
        # 中文可提取（无乱码）且关键内容都在
        assert "苹果公司" in text
        assert "投资研究报告" in text
        assert "0000320193" in text
        assert "391,035" in text
    finally:
        doc.close()


def test_render_creates_clickable_links(tmp_path: Path) -> None:
    out = tmp_path / "links.pdf"
    MarkdownPdfRenderer().render(_sample_markdown(), out)

    doc = pymupdf.open(out)
    try:
        links = [ln for page in doc for ln in page.get_links()]
        assert len(links) >= 1
        uris = {ln.get("uri") for ln in links}
        assert URL_SAMPLE in uris
        for ln in links:
            assert ln.get("from") is not None
            assert ln["from"].width > 0 and ln["from"].height > 0
    finally:
        doc.close()


def test_render_paginates_long_content(tmp_path: Path) -> None:
    paragraphs = "\n\n".join(f"第 {i} 段内容：投资研究自动化系统的测试文本。" for i in range(300))
    out = tmp_path / "long.pdf"
    MarkdownPdfRenderer().render(paragraphs, out)

    doc = pymupdf.open(out)
    try:
        assert len(doc) > 1
        text = "\n".join(page.get_text() for page in doc)
        assert "第 0 段内容" in text
        assert "第 299 段内容" in text
        # 页脚页码存在
        assert "第 1 /" in text
    finally:
        doc.close()


def test_render_handles_markdown_subset(tmp_path: Path) -> None:
    md = (
        "# 标题一\n"
        "## 标题二\n"
        "### 标题三\n"
        "- 无序项 A\n"
        "- 无序项 B\n"
        "1. 有序项一\n"
        "2. 有序项二\n"
        "> 引用内容\n"
        "---\n"
        "| 列一 | 列二 |\n"
        "| 值1 | 值2 |\n"
        "普通段落文本。"
    )
    out = tmp_path / "sub.pdf"
    MarkdownPdfRenderer().render(md, out)
    doc = pymupdf.open(out)
    try:
        text = "\n".join(page.get_text() for page in doc)
        expected = (
            "标题一",
            "标题二",
            "标题三",
            "无序项 A",
            "有序项一",
            "引用内容",
            "列一",
            "普通段落文本",
        )
        for item in expected:
            assert item in text
    finally:
        doc.close()


def test_render_empty_markdown_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MarkdownPdfRenderer().render("   \n", tmp_path / "empty.pdf")
    with pytest.raises(ValueError):
        MarkdownPdfRenderer().render_to_bytes("")


def test_render_deterministic_pages_and_text(tmp_path: Path) -> None:
    renderer = MarkdownPdfRenderer()
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    renderer.render(_sample_markdown(), a)
    renderer.render(_sample_markdown(), b)

    da = pymupdf.open(a)
    db = pymupdf.open(b)
    try:
        assert len(da) == len(db)
        text_a = "\n".join(page.get_text() for page in da)
        text_b = "\n".join(page.get_text() for page in db)
        assert text_a == text_b
    finally:
        da.close()
        db.close()


def test_render_title_heading_on_first_page(tmp_path: Path) -> None:
    out = tmp_path / "title.pdf"
    MarkdownPdfRenderer().render(_sample_markdown(), out, title="示例报告标题")
    doc = pymupdf.open(out)
    try:
        first = doc[0].get_text()
        assert "示例报告标题" in first
    finally:
        doc.close()


def test_render_to_bytes_matches_file_output(tmp_path: Path) -> None:
    out = tmp_path / "bytes.pdf"
    data = MarkdownPdfRenderer().render_to_bytes(_sample_markdown())
    assert data.startswith(b"%PDF")
    out.write_bytes(data)
    doc = pymupdf.open(out)
    try:
        assert "苹果公司" in doc[0].get_text()
    finally:
        doc.close()


def test_render_page_png_produces_image(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    MarkdownPdfRenderer().render(_sample_markdown(), pdf)
    png = tmp_path / "page1.png"
    render_page_png(pdf, 0, png, dpi=100)
    assert png.exists()
    assert png.stat().st_size > 1000
    # 尺寸合理（A4 @100dpi ≈ 827x1169）
    import struct

    with png.open("rb") as f:
        header = f.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", header[16:24])
    assert width > 500 and height > 700


def test_render_page_png_out_of_range_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    MarkdownPdfRenderer().render(_sample_markdown(), pdf)
    with pytest.raises(IndexError):
        render_page_png(pdf, 99, tmp_path / "nope.png")
