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
