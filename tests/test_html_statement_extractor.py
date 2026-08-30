"""tests/test_html_statement_extractor.py"""

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
