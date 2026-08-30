"""tests/test_html_statement_extractor.py"""

from pathlib import Path

import pytest

from invest_research.financial.annual_statements import FinancialStatementKind
from invest_research.financial.html_statement_extractor import extract_financial_tables

_HTML = """
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Income</div>
<table>
<tr><td>Revenue</td><td>72,880</td></tr>
<tr><td>Net income</td><td>29,760</td></tr>
</table>
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Balance Sheets</div>
<table>
<tr><td>Assets</td><td>100</td></tr>
<tr><td>Total liabilities and shareholders' equity</td><td>100</td></tr>
</table>
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Cash Flows</div>
<table>
<tr><td>Cash flows from operating activities:</td><td>50</td></tr>
<tr><td>Change in cash and cash equivalents</td><td>1,309</td></tr>
</table>
"""


def test_extracts_three_statements_in_document_order() -> None:
    statements = extract_financial_tables(_HTML)
    assert len(statements) == 3
    kinds = [s.kind for s in statements]
    assert kinds == [
        FinancialStatementKind.INCOME_STATEMENT,
        FinancialStatementKind.BALANCE_SHEET,
        FinancialStatementKind.CASH_FLOW,
    ]


def test_cash_flow_keeps_nonstandard_rows() -> None:
    statements = extract_financial_tables(_HTML)
    cash = next(s for s in statements if s.kind is FinancialStatementKind.CASH_FLOW)
    row_labels = [row[0] for row in cash.rows]
    assert "Change in cash and cash equivalents" in row_labels


def test_kind_detection_uses_row_features() -> None:
    from invest_research.financial.html_statement_extractor import _detect_statement_kind

    assert (
        _detect_statement_kind(["Assets", "Total liabilities"])
        is FinancialStatementKind.BALANCE_SHEET
    )
    assert (
        _detect_statement_kind(["Revenue", "Net income per share"])
        is FinancialStatementKind.INCOME_STATEMENT
    )
    assert (
        _detect_statement_kind(["Cash flows from operating activities:"])
        is FinancialStatementKind.CASH_FLOW
    )
    assert _detect_statement_kind(["Some other table"]) is None


def test_returns_one_canonical_per_kind() -> None:
    html = """
    <div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Income</div>
    <table>
    <tr><td>Revenue</td><td>72,880</td></tr>
    <tr><td>Net income</td><td>29,760</td></tr>
    <tr><td>Net income per share</td><td>1.19</td></tr>
    </table>
    <p>Some prose mentioning Consolidated Balance Sheets far away from the real table</p>
    <table>
    <tr><td>Spurious row A</td><td>1</td></tr>
    <tr><td>Spurious row B</td><td>2</td></tr>
    </table>
    <div>NVIDIA Corporation and Subsidiaries<br>Consolidated Balance Sheets</div>
    <table>
    <tr><td>Assets</td><td>100</td></tr>
    <tr><td>Cash and cash equivalents</td><td>10</td></tr>
    <tr><td>Accounts receivable</td><td>20</td></tr>
    <tr><td>Inventories</td><td>15</td></tr>
    <tr><td>Total assets</td><td>100</td></tr>
    <tr><td>Total liabilities</td><td>40</td></tr>
    <tr><td>Shareholders' equity</td><td>60</td></tr>
    <tr><td>Total liabilities and shareholders' equity</td><td>100</td></tr>
    <tr><td>Row 9</td><td>x</td></tr>
    <tr><td>Row 10</td><td>x</td></tr>
    </table>
    <div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Cash Flows</div>
    <table>
    <tr><td>Cash flows from operating activities:</td><td>50</td></tr>
    <tr><td>Net cash provided by operating activities</td><td>50</td></tr>
    </table>
    """
    statements = extract_financial_tables(html)
    assert len(statements) == 3
    assert {s.kind for s in statements} == {
        FinancialStatementKind.INCOME_STATEMENT,
        FinancialStatementKind.BALANCE_SHEET,
        FinancialStatementKind.CASH_FLOW,
    }
    balance = next(s for s in statements if s.kind is FinancialStatementKind.BALANCE_SHEET)
    assert len(balance.rows) == 10


def test_nvda_real_html_returns_three() -> None:
    html_path = Path(__file__).resolve().parents[1] / "_tmp_nvda_source.html"
    if not html_path.exists():
        pytest.skip("缺少 _tmp_nvda_source.html（gitignored，仅本地有）")
    statements = extract_financial_tables(html_path.read_text(encoding="utf-8", errors="ignore"))
    assert len(statements) == 3
    assert {s.kind for s in statements} == {
        FinancialStatementKind.INCOME_STATEMENT,
        FinancialStatementKind.BALANCE_SHEET,
        FinancialStatementKind.CASH_FLOW,
    }
