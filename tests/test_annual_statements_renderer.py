"""P07 三张财务报表 Markdown 渲染测试（数字原封不动）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.financial.annual_statements import (
    BalanceCheckResult,
    FinancialStatementKind,
    FinancialStatementRow,
    FinancialStatementSet,
    StatementPeriodType,
)
from invest_research.reporting.annual_statements_renderer import render_statements


def _row(
    label: str, value: str | None, comparator_value: str | None = None
) -> FinancialStatementRow:
    return FinancialStatementRow(
        label=label,
        concept="concept",
        unit="USD",
        period_type=StatementPeriodType.INSTANT,
        period_end=date(2026, 6, 30),
        accession_number="acc",
        value=Decimal(value) if value is not None else None,
        comparator_value=Decimal(comparator_value) if comparator_value is not None else None,
    )


def test_render_statements_outputs_tables_with_original_values() -> None:
    stmt = FinancialStatementSet(
        kind=FinancialStatementKind.BALANCE_SHEET,
        label="资产负债表",
        target_year=2026,
        comparator_year=2025,
        rows=(
            _row("资产总计", "1000", "900"),
            _row("存货", None),
        ),
        balance_check=BalanceCheckResult(ok=True, message="资产 = 负债 + 权益 成立"),
    )
    markdown = render_statements((stmt,))

    assert "## 财务报表" in markdown
    assert "### 资产负债表" in markdown
    assert "| 项目 | 本期（FY 2026） | 上期（FY 2025） | 单位 |" in markdown
    # 数字原封不动（Decimal 十进制串，无 LLM 改写）。
    assert "| 资产总计 | 1000 | 900 | USD |" in markdown
    assert "| 存货 | N/A | N/A | USD |" in markdown
    assert "勾稽校验：资产 = 负债 + 权益 成立" in markdown


def test_render_statements_without_comparator_shows_single_year() -> None:
    stmt = FinancialStatementSet(
        kind=FinancialStatementKind.INCOME_STATEMENT,
        label="利润表",
        target_year=2026,
        rows=(_row("收入", "500"),),
    )
    markdown = render_statements((stmt,), include_comparator=False)
    assert "| 项目 | FY 2026 | 单位 |" in markdown
    assert "| 收入 | 500 | USD |" in markdown


def test_render_statements_escapes_cells() -> None:
    stmt = FinancialStatementSet(
        kind=FinancialStatementKind.BALANCE_SHEET,
        label="资产负债表",
        target_year=2026,
        rows=(_row("a|b", "1"),),
    )
    markdown = render_statements((stmt,), include_comparator=False)
    assert "a\\|b" in markdown


def test_render_statements_empty_returns_empty_string() -> None:
    assert render_statements(()) == ""
