"""tests/test_html_statements_renderer.py
报表行名中英对照 + HTML 原表渲染器测试。
Ruling 1：表头 = 原表首行（真实表头，如 "Year Ended" + 各财年日期），首格翻译、其余原样，
不使用"列 N"占位列头；数据行 = rows[1:]，首格翻译、数字原封不动。
"""

from __future__ import annotations

from invest_research.financial.annual_statements import FinancialStatementKind
from invest_research.financial.html_statement_extractor import HtmlStatement
from invest_research.financial.statement_cn_labels import translate_statement_row
from invest_research.reporting.html_statements_renderer import render_html_statements


def test_translate_common_rows() -> None:
    assert translate_statement_row("Net income") == "净利润"
    assert (
        translate_statement_row("Change in cash and cash equivalents") == "现金及现金等价物净变动"
    )
    assert translate_statement_row("Revenue") == "营业收入"
    assert translate_statement_row("UnknownCustomRow") == "UnknownCustomRow"


def test_translate_suffix_fallback() -> None:
    # 未命中的完整行名按 "后缀词 + 已知词" 回退（如 "Total X" → "X合计"）。
    assert translate_statement_row("Total Operating income") == "营业利润合计"
    # 后缀词后的剩余部分不命中对照表时返回原文（不阻塞、不猜）。
    assert translate_statement_row("Total revenues") == "Total revenues"


def test_render_keeps_all_rows_and_chinese_labels() -> None:
    cash = HtmlStatement(
        kind=FinancialStatementKind.CASH_FLOW,
        rows=(
            ("Cash flows from operating activities:",),
            ("Net income", "72,880"),
            ("Change in cash and cash equivalents", "1,309"),
        ),
        source_table_index=0,
    )
    md = render_html_statements((cash,))
    assert "## 财务报表" in md
    assert "净利润" in md
    assert "1,309" in md
    # Ruling 1：首行成为表头，首格同样译中文，行名不被丢弃。
    assert "经营活动现金流量：" in md
    assert "Cash flows from operating activities" not in md
    assert "Change in cash and cash equivalents" not in md  # 已译成中文
    # Ruling 1：不使用"列 N"占位列头。
    assert "列 1" not in md
    assert "列 2" not in md


def test_render_uses_real_header_row_verbatim() -> None:
    income = HtmlStatement(
        kind=FinancialStatementKind.INCOME_STATEMENT,
        rows=(
            ("Year Ended", "Jan 26, 2025", "Jan 28, 2024", "(In millions)"),
            ("Revenue", "130,005", "109,650", "97,703"),
            ("Net income", "72,880", "52,033", "41,713"),
        ),
        source_table_index=0,
    )
    md = render_html_statements((income,))
    assert "## 财务报表" in md
    assert "### 利润表" in md
    # Ruling 1：表头行保留原表真实内容（日期、单位原样），不用"列 N"占位。
    assert "| Year Ended | Jan 26, 2025 | Jan 28, 2024 | (In millions) |" in md
    assert "|---|---|---|---|" in md
    assert "列 1" not in md
    # 行名列翻译为中文，数字列原封不动。
    assert "| 营业收入 | 130,005 | 109,650 | 97,703 |" in md
    assert "| 净利润 | 72,880 | 52,033 | 41,713 |" in md
    assert "Revenue" not in md
    assert "Net income" not in md


def test_render_preserves_nonstandard_rows_verbatim() -> None:
    balance = HtmlStatement(
        kind=FinancialStatementKind.BALANCE_SHEET,
        rows=(
            ("As of", "Jan 26, 2025", "Jan 28, 2024"),
            ("Total assets", "182,783", "168,088"),
            ("SomeCustomRowNotInDict", "1,234", "567"),
        ),
        source_table_index=0,
    )
    md = render_html_statements((balance,))
    assert "### 资产负债表" in md
    # 未命中的行名保留原文（不阻塞、不猜），数字原样。
    assert "SomeCustomRowNotInDict" in md
    assert "| 资产总计 | 182,783 | 168,088 |" in md
    assert "| SomeCustomRowNotInDict | 1,234 | 567 |" in md


def test_render_empty_statements_returns_empty_string() -> None:
    assert render_html_statements(()) == ""


def test_render_aligns_by_year_columns_without_na_noise() -> None:
    """GOOGL 风格 colspan 表：按年份列对齐渲染，空值用 · 而非 N/A。"""
    stmt = HtmlStatement(
        kind=FinancialStatementKind.BALANCE_SHEET,
        # 行名x3 | 2024 | 2025 展开后的原始行（含 $ 与空列）
        rows=(
            ("", "", "", "As of December 31,", "", "", "", "", "", "", ""),
            ("", "", "", "2024", "", "", "2025", "", "", "", ""),
            ("现金及现金等价物", "", "", "$", "23,466", "", "$", "30,708", "", "", ""),
        ),
        year_columns=("2024", "2025"),
        source_table_index=0,
    )
    md = render_html_statements((stmt,))
    assert "2024" in md and "2025" in md
    assert "N/A" not in md  # 干净对齐，无 N/A 噪音
    assert "$ 23,466" in md or "23,466" in md


def test_render_aligns_three_year_cash_flow() -> None:
    """现金流量表三年份：数值按年份对齐，缺值行显示 ·。"""
    stmt = HtmlStatement(
        kind=FinancialStatementKind.CASH_FLOW,
        rows=(
            ("", "Year Ended December 31,"),
            ("", "2023", "", "2024", "", "2025"),
            ("Net income", "$", "73,795", "", "", "$", "100,118", "", "", "$", "132,170", ""),
            ("Depreciation", "11,946", "", "", "15,311", "", "", "21,136", ""),
            ("Operating activities", "", "", "", "", ""),
        ),
        year_columns=("2023", "2024", "2025"),
        source_table_index=0,
    )
    md = render_html_statements((stmt,))
    assert "2023" in md and "2024" in md and "2025" in md
    assert "N/A" not in md
    assert "73,795" in md and "15,311" in md
    assert "·" in md  # 缺值行用 · 占位，而非 N/A


def test_render_merges_consecutive_number_cells_in_one_year_column() -> None:
    """数字[x2] colspan 覆盖两格数值：同一财年列内连续数值格合并为单个值。"""
    stmt = HtmlStatement(
        kind=FinancialStatementKind.BALANCE_SHEET,
        # 2024 列数值拆成 "1,23"+"4,567" 两格（数字[x2] 结构），应合并为 1,234,567。
        rows=(
            ("", "", "", "As of December 31,", "", "", "", "", "", "", ""),
            ("", "", "", "2024", "", "", "2025", "", "", "", ""),
            ("现金及现金等价物", "", "", "1,23", "4,567", "", "", "30,708", "", "", ""),
        ),
        year_columns=("2024", "2025"),
        source_table_index=0,
    )
    md = render_html_statements((stmt,))
    assert "1,234,567" in md  # 两格数值合并为一个值，而非当作两个财年
    assert "| 现金及现金等价物 | 1,234,567 | 30,708 |" in md
