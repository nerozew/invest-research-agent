"""P07 年度报告渲染层优化：纯函数单测（不调用 LLM/DB/网络）。

覆盖风险：
- 正文 ``[src_xxx]`` → ``[来源名](url)`` 链接化（fr_ 事实、未知 key、缺 URL、杂散
  符号、标签清洗）；
- 官方声明 best-effort 定位（302 缺失 / 空文档不抛异常）；
- SEC 来源名启发式与回退；
- 封面确定性（generated_at 可注入）与降级。
"""

from datetime import datetime

from invest_research.application.citation_registry import (
    CITATION_TYPE_FACT,
    CITATION_TYPE_SOURCE,
    CitationRegistry,
    CitationRegistryEntry,
)
from invest_research.infrastructure.annual_document_pipeline import AnnualParsedTextBlock
from invest_research.reporting.annual_report_renderer import (
    AnnualOfficialStatements,
    OfficialStatement,
    _statement_lines,
    build_annual_cover,
    build_mda_section,
    build_mda_summary_section,
    build_reference_list,
    extract_document_title,
    extract_mda,
    extract_official_statements,
    human_kind,
    render_citation_links,
    render_citation_numbers,
)

SRC_URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"


def _registry(*entries: CitationRegistryEntry) -> CitationRegistry:
    return CitationRegistry(entries=list(entries))


def _src_entry(
    key: str = "src_a1b2c3d4e5f6", title: str = "Apple Inc. 10-K"
) -> CitationRegistryEntry:
    return CitationRegistryEntry(
        citation_key=key, citation_type=CITATION_TYPE_SOURCE, title=title, canonical_url=SRC_URL
    )


def _fact_entry(key: str = "fr_0123456789ab", title: str = "营业收入") -> CitationRegistryEntry:
    return CitationRegistryEntry(
        citation_key=key, citation_type=CITATION_TYPE_FACT, title=title, canonical_url=None
    )


def test_render_citation_links_converts_src_to_plain_source_name():
    body = "营业收入增长显著 [src_a1b2c3d4e5f6]。"
    out = render_citation_links(body, _registry(_src_entry()))
    # 正文只显示来源名纯文本（不带 URL）；可点击链接只在末尾来源清单。
    assert "[Apple Inc. 10-K]" in out
    assert "](" not in out
    assert "[src_a1b2c3d4e5f6]" not in out


def test_render_citation_links_keeps_fact_citations_unchanged():
    body = "本期收入为 100 [fr_0123456789ab]。"
    out = render_citation_links(body, _registry(_fact_entry()))
    assert "[fr_0123456789ab]" in out
    assert "](http" not in out


def test_render_citation_links_keeps_unknown_keys():
    body = "未知引用 [src_ffffffffffff]。"
    out = render_citation_links(body, _registry(_src_entry()))
    assert "[src_ffffffffffff]" in out


def test_render_citation_links_source_without_url_stays_plain():
    entry = CitationRegistryEntry(
        citation_key="src_a1b2c3d4e5f6",
        citation_type=CITATION_TYPE_SOURCE,
        title="无 URL",
        canonical_url=None,
    )
    out = render_citation_links("[src_a1b2c3d4e5f6]", _registry(entry))
    assert "[src_a1b2c3d4e5f6]" in out


def test_render_citation_links_preserves_locator_and_brackets():
    body = "段落 [src_a1b2c3d4e5f6] locator=offset:42（[杂散]）。"
    out = render_citation_links(body, _registry(_src_entry()))
    assert "[Apple Inc. 10-K] locator=offset:42" in out
    assert "](" not in out
    assert "（[杂散]）" in out


def test_render_citation_links_strips_brackets_from_label():
    entry = _src_entry(title="Apple [Inc.] 10-K")
    out = render_citation_links("[src_a1b2c3d4e5f6]", _registry(entry))
    # 方括号被清洗：标签内不再有嵌套括号，正文为纯文本来源名。
    assert "[Apple Inc. 10-K]" in out
    assert "[Apple [Inc.]" not in out
    assert "](" not in out


def test_extract_official_statements_cover_and_audit_opinion():
    blocks = (
        AnnualParsedTextBlock(
            text="UNITED STATES SECURITIES AND EXCHANGE COMMISSION FORM 10-K", locator="offset:0"
        ),
        AnnualParsedTextBlock(
            text="...financial statements present fairly, in all material respects...",
            locator="offset:5000",
        ),
        AnnualParsedTextBlock(text="风险因素正文", locator="offset:8000"),
    )
    statements = extract_official_statements(blocks)
    assert statements.cover_page is not None
    assert statements.cover_page.locator == "offset:0"
    assert statements.audit_opinion is not None
    assert statements.audit_opinion.locator == "offset:5000"
    assert statements.certification_302 is None


def test_extract_official_statements_missing_302_does_not_raise():
    blocks = (AnnualParsedTextBlock(text="普通正文没有认证措辞", locator="offset:1"),)
    statements = extract_official_statements(blocks)
    assert statements.cover_page is None
    assert statements.audit_opinion is None
    assert statements.certification_302 is None


def test_extract_official_statements_empty_blocks():
    statements = extract_official_statements(())
    assert statements.is_empty


def test_extract_mda_finds_item7_verbatim():
    blocks = (
        AnnualParsedTextBlock(text="Item 1A. Risk Factors", locator="offset:1000"),
        AnnualParsedTextBlock(
            text="Item 7. Management's Discussion and Analysis", locator="offset:5000"
        ),
        AnnualParsedTextBlock(
            text=(
                "Revenue increased 30% due to strong data center demand, reflecting continued "
                "adoption of AWS across customer verticals and geographic expansion."
            ),
            locator="offset:5100",
        ),
        AnnualParsedTextBlock(
            text=(
                "We remain focused on operating leverage as we scale our fulfillment network "
                "and invest in generative AI capabilities to improve customer experience."
            ),
            locator="offset:5300",
        ),
        AnnualParsedTextBlock(
            text="Item 8. Financial Statements and Supplementary Data", locator="offset:9000"
        ),
    )
    mda = extract_mda(blocks)
    assert mda is not None
    assert mda.locator == "offset:5000"
    assert "Revenue increased 30%" in mda.text
    assert "operating leverage" in mda.text
    # 遇到 Item 8 即停，不串进财务报表章节。
    assert "Item 8" not in mda.text


def test_extract_mda_missing_returns_none():
    blocks = (AnnualParsedTextBlock(text="普通正文没有管理层讨论", locator="offset:1"),)
    assert extract_mda(blocks) is None


def test_build_mda_section_renders_heading_and_verbatim():
    blocks = (
        AnnualParsedTextBlock(
            text="Item 7. Management's Discussion and Analysis", locator="offset:5000"
        ),
        AnnualParsedTextBlock(
            text=(
                "Revenue increased 30% due to strong data center demand, reflecting continued "
                "adoption across customer verticals and our focus on operating leverage as we "
                "scale the fulfillment network and expand internationally."
            ),
            locator="offset:5100",
        ),
    )
    section = build_mda_section(blocks)
    assert section.startswith("## 管理层讨论与分析")
    assert "Revenue increased 30%" in section
    assert "原文直取" in section
    assert "offset:5000" in section


def test_build_mda_section_empty_when_missing():
    blocks = (AnnualParsedTextBlock(text="普通正文", locator="offset:1"),)
    assert build_mda_section(blocks) == ""


def test_extract_document_title_heuristic():
    blocks = (
        AnnualParsedTextBlock(
            text="Annual report pursuant to Section 13 and 15(d)", locator="offset:10"
        ),
        AnnualParsedTextBlock(text="FORM 10-K", locator="offset:0"),
    )
    title = extract_document_title(blocks)
    assert title is not None
    assert "10-K" in title


def test_extract_document_title_falls_back_to_none():
    blocks = (AnnualParsedTextBlock(text="普通正文没有表单关键字", locator="offset:0"),)
    assert extract_document_title(blocks) is None


def test_build_annual_cover_contains_required_fields():
    cover = build_annual_cover(
        legal_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        as_of_date="2025-10-31",
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        generated_at=datetime(2025, 11, 1, 0, 0, 0),
    )
    assert "分析基准日" in cover
    assert "2025-10-31" in cover
    assert "财报期间" in cover
    assert "FY2025 对比 FY2024" in cover
    assert "报告生成时间" in cover
    assert "2025-11-01T00:00:00" in cover


def test_build_annual_cover_deterministic():
    kwargs = dict(
        legal_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        as_of_date="2025-10-31",
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        generated_at=datetime(2025, 11, 1, 0, 0, 0),
    )
    assert build_annual_cover(**kwargs) == build_annual_cover(**kwargs)


def test_build_annual_cover_omits_official_section_when_none():
    cover = build_annual_cover(
        legal_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        as_of_date="2025-10-31",
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        generated_at=datetime(2025, 11, 1, 0, 0, 0),
    )
    assert "官方声明摘录" not in cover


def test_human_kind_mapping():
    assert human_kind("target_annual_filing") == "目标年度 10-K"
    assert human_kind("company_facts") == "SEC Company Facts（XBRL）"
    assert human_kind("unknown_kind") == "unknown_kind"


def test_statement_lines_renders_english_and_chinese_translation():
    statement = OfficialStatement(
        text="In our opinion, the consolidated financial statements present fairly...",
        locator="offset:100",
        translation="我们认为，合并财务报表在所有重大方面公允列报……",
    )
    lines = _statement_lines("独立审计意见", statement)
    rendered = "\n".join(lines)
    assert "**独立审计意见**（定位：offset:100）" in rendered
    assert "> In our opinion" in rendered          # 英文原文保留（出处）
    assert "**中文翻译**" in rendered                # 中文翻译追加
    assert "我们认为，合并财务报表" in rendered


def test_statement_lines_without_translation_stays_english_only():
    statement = OfficialStatement(text="English only text.", locator="offset:1")
    rendered = "\n".join(_statement_lines("封面页", statement))
    assert "> English only text." in rendered
    assert "**中文翻译**" not in rendered            # 翻译缺失/失败时不伪造


def test_build_mda_summary_section_renders_chinese_summary():
    summary = "管理层认为收入增长主要由 AWS 驱动，并计划继续扩大海外布局。"
    section = build_mda_summary_section(summary, locator="offset:500")
    assert "## 管理层讨论与分析" in section
    assert "要点摘译（中文）" in section
    assert summary in section
    assert "定位 offset:500" in section


def test_build_annual_cover_includes_translation_when_present():
    statements = AnnualOfficialStatements(
        audit_opinion=OfficialStatement(
            text="present fairly, in all material respects...",
            locator="offset:55",
            translation="公允列报……",
        )
    )
    cover = build_annual_cover(
        legal_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        as_of_date="2025-10-31",
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        generated_at=datetime(2025, 11, 1, 0, 0, 0),
        official_statements=statements,
    )
    assert "**中文翻译**" in cover
    assert "公允列报" in cover
    assert "present fairly" in cover              # 英文原文仍保留


def test_extract_mda_skips_toc_item7_and_finds_body(monkeypatch):
    """AMZN 10-K 的 Item 7 在目录和正文各出现一次：须跳过目录命中找正文。"""
    blocks = (
        # 目录页的 Item 7 条目：标题 + 紧跟短条目（页码/Item 7A 目录），无实质正文。
        AnnualParsedTextBlock(
            text="Item 7. Management's Discussion and Analysis", locator="offset:100"
        ),
        AnnualParsedTextBlock(
            text="Item 7A. Quantitative and Qualitative Disclosures", locator="offset:120"
        ),
        AnnualParsedTextBlock(text="Item 8. Financial Statements", locator="offset:130"),
        AnnualParsedTextBlock(text="7", locator="offset:140"),  # 页码
        # 正文区的 Item 7：标题 + 长正文段落。
        AnnualParsedTextBlock(
            text=(
                "Item 7. Management's Discussion and Analysis of Financial Condition "
                "and Results of Operations"
            ),
            locator="offset:500",
        ),
        AnnualParsedTextBlock(
            text=(
                "This Annual Report on Form 10-K includes forward-looking statements "
                "within the meaning of federal securities laws. Our primary source of "
                "revenue is the sale of a wide range of consumer products and services, "
                "and we continue to invest in technology and infrastructure."
            ),
            locator="offset:520",
        ),
    )
    mda = extract_mda(blocks)
    assert mda is not None
    assert mda.locator == "offset:500"  # 定位到正文区的 Item 7
    assert "forward-looking statements" in mda.text
    assert "primary source of revenue" in mda.text
    assert "Item 7A" not in mda.text  # 不含目录条目


def test_extract_mda_skips_long_cross_reference_block():
    """风险因素里交叉引用 MD&A 的长正文块不应被当作 Item 7 标题（AMZN 真实事故）。"""
    blocks = (
        # 风险因素概述里整段交叉引用 MD&A（长正文，非标题，如 AMZN offset:45656 的 1273 字符块）。
        AnnualParsedTextBlock(
            text=(
                "Please carefully consider the following discussion of significant factors, "
                "events and uncertainties that may affect the company, its business, financial "
                "condition, results of operations and cash flows, including the discussion "
                "under the heading Management's Discussion and Analysis of Financial Condition "
                "and Results of Operations, together with the other information contained in "
                "this Annual Report on Form 10-K and the risk factors discussed in Item 1A, "
                "which should be read in conjunction with our consolidated financial statements "
                "and related notes included elsewhere in this filing."
            ),
            locator="offset:45656",
        ),
        AnnualParsedTextBlock(
            text="We face intense competition in a rapidly evolving market.", locator="offset:45800"
        ),
        # 真正的 Item 7 标题 + 正文。
        AnnualParsedTextBlock(
            text="Item 7. Management's Discussion and Analysis", locator="offset:110839"
        ),
        AnnualParsedTextBlock(
            text=(
                "This Annual Report on Form 10-K includes forward-looking statements. "
                "Our primary source of revenue is the sale of a wide range of products, "
                "and we continue to invest heavily in technology and infrastructure to "
                "drive long-term growth across our segments."
            ),
            locator="offset:110950",
        ),
    )
    mda = extract_mda(blocks)
    assert mda is not None
    assert mda.locator == "offset:110839"  # 定位到真正的 Item 7，而非风险交叉引用
    assert "forward-looking statements" in mda.text
    assert "fierce competition" not in mda.text  # 不摘风险内容


def test_render_citation_numbers_assigns_sequence_and_dedups():
    body = "先引 [src_a1b2c3d4e5f6] 再引 [fr_0123456789ab] 再引 [src_a1b2c3d4e5f6]。"
    out, mapping = render_citation_numbers(body, _registry(_src_entry(), _fact_entry()))
    assert out == "先引 [1] 再引 [2] 再引 [1]。"
    assert mapping == {"src_a1b2c3d4e5f6": 1, "fr_0123456789ab": 2}


def test_render_citation_numbers_keeps_unknown_and_locator():
    body = "未知 [src_ffffffffffff] locator=offset:42 残留 [fr_0123456789ab]。"
    out, mapping = render_citation_numbers(body, _registry(_fact_entry()))
    assert "[src_ffffffffffff]" in out
    assert "locator=offset:42" in out
    assert mapping == {"fr_0123456789ab": 1}


def test_build_reference_list_links_sources_and_labels_facts():
    registry = _registry(_src_entry(), _fact_entry(title="营业收入"))
    mapping = {"src_a1b2c3d4e5f6": 1, "fr_0123456789ab": 2}
    out = build_reference_list(registry, mapping)
    assert f"[1] [Apple Inc. 10-K]({SRC_URL})" in out
    assert "[2] 营业收入（SEC XBRL 事实）" in out


def test_build_reference_list_sorted_by_number_and_empty():
    registry = _registry(_src_entry(), _fact_entry())
    out = build_reference_list(registry, {"fr_0123456789ab": 2, "src_a1b2c3d4e5f6": 1})
    # 按编号顺序（[1] 在 [2] 前），不按 dict 插入顺序。
    assert out.index("[1] [Apple Inc. 10-K]") < out.index("[2] 营业收入（SEC XBRL 事实）")
    assert build_reference_list(registry, {}) == ""
