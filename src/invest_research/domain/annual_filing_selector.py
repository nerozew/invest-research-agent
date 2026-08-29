"""P07-02：按报告期选择年度 10-K 的纯领域规则。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.models import Filing

_ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})


class AnnualFilingSelectionStatus(StrEnum):
    """年度 filing 选择结果；缺失是可解释结果而不是异常。"""

    READY = "ready"
    COMPARATOR_MISSING = "comparator_missing"
    TARGET_MISSING = "target_missing"


class AnnualFilingSelection(BaseModel):
    """FY Y 与 FY Y-1 的 canonical filing 及原始版审计关联。"""

    model_config = ConfigDict(frozen=True)

    status: AnnualFilingSelectionStatus
    target_fiscal_year: int | None = Field(default=None, ge=1900, le=9999)
    target_filing: Filing | None = None
    target_original_accession_number: str | None = None
    comparator_fiscal_year: int | None = Field(default=None, ge=1900, le=9999)
    comparator_filing: Filing | None = None
    comparator_original_accession_number: str | None = None
    missing_reason: str | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> "AnnualFilingSelection":
        if self.status is AnnualFilingSelectionStatus.READY:
            if self.target_filing is None or self.comparator_filing is None:
                raise ValueError("ready 结果必须包含目标与上一年度 filing")
            if self.missing_reason is not None:
                raise ValueError("ready 结果不能提供 missing_reason")
        elif self.status is AnnualFilingSelectionStatus.COMPARATOR_MISSING:
            if self.target_filing is None or not self.missing_reason:
                raise ValueError("comparator_missing 必须包含目标 filing 和缺失原因")
            if self.comparator_filing is not None:
                raise ValueError("comparator_missing 不能包含 comparator filing")
        else:
            if self.target_filing is not None or not self.missing_reason:
                raise ValueError("target_missing 不能包含目标 filing，且必须提供缺失原因")
            if self.comparator_filing is not None:
                raise ValueError("target_missing 不能包含 comparator filing")
        return self


class AnnualFilingSelector:
    """按 ``report_period`` 而非提交日期选择年度 10-K/10-K/A。"""

    @staticmethod
    def select(
        filings: tuple[Filing, ...] | list[Filing], *, as_of_date: date
    ) -> AnnualFilingSelection:
        """选择截至日期可用的最新 FY Y 与严格 FY Y-1。

        同一报告期有多个 filing 时，提交日期最新者为 canonical；如是修订版，会
        额外保留对应最新原始 10-K 的 accession，供后续审计使用。
        """
        eligible = [
            filing
            for filing in filings
            if filing.form_type in _ANNUAL_FORMS
            and filing.report_period is not None
            and filing.filing_date <= as_of_date
        ]
        if not eligible:
            return AnnualFilingSelection(
                status=AnnualFilingSelectionStatus.TARGET_MISSING,
                missing_reason="截至数据截止日未找到带 reportDate 的可用 10-K 或 10-K/A",
            )

        periods = {filing.report_period for filing in eligible if filing.report_period is not None}
        target_period = max(periods)
        target = AnnualFilingSelector._select_period(eligible, target_period)
        target_year = target_period.year
        comparator_periods = {
            filing.report_period
            for filing in eligible
            if filing.report_period is not None and filing.report_period.year == target_year - 1
        }
        if not comparator_periods:
            return AnnualFilingSelection(
                status=AnnualFilingSelectionStatus.COMPARATOR_MISSING,
                target_fiscal_year=target_year,
                target_filing=target,
                target_original_accession_number=AnnualFilingSelector._original_accession(
                    eligible, target_period
                ),
                comparator_fiscal_year=target_year - 1,
                missing_reason=f"FY {target_year - 1} 未找到带 reportDate 的可用 10-K 或 10-K/A",
            )

        comparator_period = max(comparator_periods)
        comparator = AnnualFilingSelector._select_period(eligible, comparator_period)
        return AnnualFilingSelection(
            status=AnnualFilingSelectionStatus.READY,
            target_fiscal_year=target_year,
            target_filing=target,
            target_original_accession_number=AnnualFilingSelector._original_accession(
                eligible, target_period
            ),
            comparator_fiscal_year=comparator_period.year,
            comparator_filing=comparator,
            comparator_original_accession_number=AnnualFilingSelector._original_accession(
                eligible, comparator_period
            ),
        )

    @staticmethod
    def _select_period(filings: list[Filing], report_period: date) -> Filing:
        candidates = [filing for filing in filings if filing.report_period == report_period]
        return max(
            candidates,
            key=lambda filing: (
                filing.filing_date,
                filing.is_amendment,
                filing.accession_number,
            ),
        )

    @staticmethod
    def _original_accession(filings: list[Filing], report_period: date) -> str | None:
        originals = [
            filing
            for filing in filings
            if filing.report_period == report_period and not filing.is_amendment
        ]
        if not originals:
            return None
        original = max(
            originals,
            key=lambda filing: (filing.filing_date, filing.accession_number),
        )
        return original.accession_number
