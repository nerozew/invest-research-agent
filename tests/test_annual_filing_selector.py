"""P07-02：年度 10-K 选择器测试。"""

from __future__ import annotations

from datetime import date

from invest_research.domain.annual_filing_selector import (
    AnnualFilingSelection,
    AnnualFilingSelectionStatus,
    AnnualFilingSelector,
)
from invest_research.domain.models import Filing


def _filing(
    accession: str,
    *,
    form: str = "10-K",
    filed: date,
    report_period: date | None,
) -> Filing:
    return Filing(
        accession_number=accession,
        form_type=form,
        filing_date=filed,
        report_period=report_period,
        primary_document_url=f"https://www.sec.gov/Archives/{accession}.htm",
        is_amendment=form.endswith("/A"),
    )


def test_selects_fiscal_year_from_report_period_not_filing_year() -> None:
    selection = AnnualFilingSelector.select(
        [
            _filing(
                "fy-2024",
                filed=date(2025, 1, 29),
                report_period=date(2024, 6, 30),
            ),
            _filing(
                "fy-2025",
                filed=date(2026, 1, 29),
                report_period=date(2025, 6, 30),
            ),
        ],
        as_of_date=date(2026, 1, 29),
    )

    assert selection.status is AnnualFilingSelectionStatus.READY
    assert selection.target_fiscal_year == 2025
    assert selection.target_filing is not None
    assert selection.target_filing.accession_number == "fy-2025"
    assert selection.comparator_fiscal_year == 2024


def test_excludes_future_filing_even_when_report_period_is_earlier() -> None:
    selection = AnnualFilingSelector.select(
        [
            _filing(
                "future-fy-2025",
                filed=date(2026, 1, 30),
                report_period=date(2025, 6, 30),
            ),
            _filing(
                "fy-2024",
                filed=date(2025, 1, 29),
                report_period=date(2024, 6, 30),
            ),
        ],
        as_of_date=date(2026, 1, 29),
    )

    assert selection.status is AnnualFilingSelectionStatus.COMPARATOR_MISSING
    assert selection.target_fiscal_year == 2024
    assert selection.target_filing is not None
    assert selection.target_filing.accession_number == "fy-2024"


def test_latest_amendment_is_canonical_and_original_accession_is_retained() -> None:
    selection = AnnualFilingSelector.select(
        [
            _filing(
                "fy-2024-original",
                filed=date(2025, 1, 29),
                report_period=date(2024, 6, 30),
            ),
            _filing(
                "fy-2025-original",
                filed=date(2026, 1, 29),
                report_period=date(2025, 6, 30),
            ),
            _filing(
                "fy-2025-amendment-1",
                form="10-K/A",
                filed=date(2026, 2, 15),
                report_period=date(2025, 6, 30),
            ),
            _filing(
                "fy-2025-amendment-2",
                form="10-K/A",
                filed=date(2026, 3, 1),
                report_period=date(2025, 6, 30),
            ),
        ],
        as_of_date=date(2026, 3, 1),
    )

    assert selection.status is AnnualFilingSelectionStatus.READY
    assert selection.target_filing is not None
    assert selection.target_filing.accession_number == "fy-2025-amendment-2"
    assert selection.target_original_accession_number == "fy-2025-original"
    assert selection.comparator_original_accession_number == "fy-2024-original"
    assert AnnualFilingSelection.model_validate_json(selection.model_dump_json()) == selection


def test_ignores_10q_and_filings_without_report_period() -> None:
    selection = AnnualFilingSelector.select(
        [
            _filing(
                "q-only",
                form="10-Q",
                filed=date(2026, 1, 29),
                report_period=date(2025, 12, 31),
            ),
            _filing(
                "missing-period",
                filed=date(2026, 1, 29),
                report_period=None,
            ),
        ],
        as_of_date=date(2026, 1, 29),
    )

    assert selection.status is AnnualFilingSelectionStatus.TARGET_MISSING
    assert selection.target_filing is None


def test_missing_comparator_is_an_explainable_result() -> None:
    selection = AnnualFilingSelector.select(
        [
            _filing(
                "fy-2025",
                filed=date(2026, 1, 29),
                report_period=date(2025, 6, 30),
            )
        ],
        as_of_date=date(2026, 1, 29),
    )

    assert selection.status is AnnualFilingSelectionStatus.COMPARATOR_MISSING
    assert selection.comparator_fiscal_year == 2024
    assert selection.comparator_filing is None
    assert selection.missing_reason is not None
