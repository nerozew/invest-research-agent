"""P07-05 年度确定性财务比较包测试。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from invest_research.domain.annual_filing_selector import (
    AnnualFilingSelection,
    AnnualFilingSelectionStatus,
)
from invest_research.domain.annual_pipeline import (
    AnnualReadiness,
    CoverageItem,
    CoverageLedger,
    CoverageRequirement,
    CoverageStatus,
)
from invest_research.domain.models import Filing, FinancialFact, MetricStatus
from invest_research.financial.annual_comparison import compute_annual_comparison
from invest_research.financial.concept_mapping import CONCEPTS_V1_PATH, load_concept_mapping
from invest_research.infrastructure.annual_company_facts_pipeline import (
    AnnualCompanyFactsArtifactResult,
    AnnualCompanyFactsSelection,
    AnnualCompanyFactsStatus,
)
from invest_research.infrastructure.annual_comparison_builder import AnnualComparisonBuilder
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualDocumentArtifactResult,
    AnnualDocumentPipelineStatus,
)
from invest_research.infrastructure.annual_evidence_fanout import AnnualEvidenceBundle
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore

JOB_ID = UUID("00000000-0000-0000-0000-000000000706")
TARGET_ACCESSION = "target-amendment"
COMPARATOR_ACCESSION = "comparator-annual"


def _filing(year: int, accession: str, *, amended: bool = False) -> Filing:
    return Filing(
        accession_number=accession,
        form_type="10-K/A" if amended else "10-K",
        filing_date=date(year + 1, 2, 15),
        report_period=date(year, 12, 31),
        primary_document_url=f"https://example.test/{accession}.htm",
        is_amendment=amended,
    )


def _selection(*, comparator: bool = True) -> AnnualFilingSelection:
    target = _filing(2024, TARGET_ACCESSION, amended=True)
    if not comparator:
        return AnnualFilingSelection(
            status=AnnualFilingSelectionStatus.COMPARATOR_MISSING,
            target_fiscal_year=2024,
            target_filing=target,
            target_original_accession_number="target-original",
            comparator_fiscal_year=2023,
            missing_reason="no comparator 10-K",
        )
    comparator_filing = _filing(2023, COMPARATOR_ACCESSION)
    return AnnualFilingSelection(
        status=AnnualFilingSelectionStatus.READY,
        target_fiscal_year=2024,
        target_filing=target,
        target_original_accession_number="target-original",
        comparator_fiscal_year=2023,
        comparator_filing=comparator_filing,
        comparator_original_accession_number=COMPARATOR_ACCESSION,
    )


def _duration(
    concept: str, value: str, *, year: int, accession: str, unit: str = "USD"
) -> FinancialFact:
    return FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        fiscal_year=year,
        fiscal_period="FY",
        form_type="10-K/A" if accession == TARGET_ACCESSION else "10-K",
        accession_number=accession,
    )


def _instant(
    concept: str, value: str, *, year: int, accession: str, unit: str = "USD"
) -> FinancialFact:
    return FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit=unit,
        instant_date=date(year, 12, 31),
        fiscal_year=year,
        fiscal_period="FY",
        form_type="10-K/A" if accession == TARGET_ACCESSION else "10-K",
        accession_number=accession,
    )


def _facts(*, include_target_revenue: bool = True) -> list[FinancialFact]:
    target = [
        _duration("GrossProfit", "40", year=2024, accession=TARGET_ACCESSION),
        _duration("OperatingIncomeLoss", "20", year=2024, accession=TARGET_ACCESSION),
        _duration("NetIncomeLoss", "10", year=2024, accession=TARGET_ACCESSION),
        _duration(
            "NetCashProvidedByUsedInOperatingActivities",
            "15",
            year=2024,
            accession=TARGET_ACCESSION,
        ),
        _duration(
            "PaymentsToAcquirePropertyPlantAndEquipment", "5", year=2024, accession=TARGET_ACCESSION
        ),
        _instant("AssetsCurrent", "60", year=2024, accession=TARGET_ACCESSION),
        _instant("LiabilitiesCurrent", "30", year=2024, accession=TARGET_ACCESSION),
        _instant("Assets", "200", year=2024, accession=TARGET_ACCESSION),
        _instant("Liabilities", "100", year=2024, accession=TARGET_ACCESSION),
    ]
    if include_target_revenue:
        target.insert(0, _duration("Revenues", "100", year=2024, accession=TARGET_ACCESSION))
    return [
        *target,
        _duration("Revenues", "80", year=2023, accession=COMPARATOR_ACCESSION),
        _duration("NetIncomeLoss", "8", year=2023, accession=COMPARATOR_ACCESSION),
        _instant("Assets", "160", year=2023, accession=COMPARATOR_ACCESSION),
        # 同年原版数值错误；canonical 规则必须忽略它。
        _duration("Revenues", "999", year=2024, accession="target-original"),
    ]


def _ref(key: str, checksum: str = "a" * 64) -> ArtifactRef:
    return ArtifactRef(artifact_key=key, byte_size=1, content_checksum=checksum)


def _ledger(*, financial_ready: bool = True, comparator_document: bool = True) -> CoverageLedger:
    def item(requirement: CoverageRequirement, satisfied: bool) -> CoverageItem:
        return CoverageItem(
            requirement=requirement,
            status=CoverageStatus.SATISFIED if satisfied else CoverageStatus.MISSING,
            evidence_artifact_keys=("evidence",) if satisfied else (),
            missing_reason=None if satisfied else "fixture missing",
        )

    target_facts = financial_ready
    comparator_facts = financial_ready
    readiness = (
        AnnualReadiness.FULL_READY
        if financial_ready and comparator_document
        else AnnualReadiness.FINANCIAL_ONLY_READY
        if financial_ready
        else AnnualReadiness.BLOCKED
    )
    return CoverageLedger(
        target_fiscal_year=2024,
        readiness=readiness,
        items=(
            item(CoverageRequirement.TARGET_ANNUAL_FILING, True),
            item(CoverageRequirement.TARGET_FINANCIAL_FACTS, target_facts),
            item(CoverageRequirement.COMPARATOR_FINANCIAL_FACTS, comparator_facts),
            item(CoverageRequirement.COMPARATOR_ANNUAL_FILING, comparator_document),
        ),
    )


def _evidence(
    tmp_path: Path,
    facts: list[FinancialFact],
    *,
    job_id: UUID = JOB_ID,
    financial_ready: bool = True,
) -> AnnualEvidenceBundle:
    store = ArtifactStore(tmp_path / str(job_id))
    selected = AnnualCompanyFactsSelection(
        cik="0000789019",
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
        available_fiscal_years=(2024, 2023),
        facts=tuple(facts),
    )
    selected_ref = store.write(
        "annual/company-facts/selected.json",
        json.dumps(selected.model_dump(mode="json"), sort_keys=True).encode(),
    )
    target_document = AnnualDocumentArtifactResult(
        status=AnnualDocumentPipelineStatus.COMPLETED,
        accession_number=TARGET_ACCESSION,
        source_artifact=_ref("annual/target/source.html"),
        parsed_artifact=_ref("annual/target/parsed.json"),
        manifest_artifact=_ref("annual/target/manifest.json"),
    )
    return AnnualEvidenceBundle(
        target_document=target_document,
        company_facts=AnnualCompanyFactsArtifactResult(
            status=AnnualCompanyFactsStatus.COMPLETED,
            source_artifact=_ref("annual/company-facts/source.json"),
            selected_artifact=selected_ref,
            manifest_artifact=_ref("annual/company-facts/manifest.json"),
            available_fiscal_years=(2024, 2023),
        ),
        coverage_ledger=_ledger(financial_ready=financial_ready),
    )


def test_builds_ten_decimal_metrics_from_canonical_amendment_and_reuses(tmp_path: Path) -> None:
    builder = AnnualComparisonBuilder(tmp_path)
    evidence = _evidence(tmp_path, _facts())

    first = builder.build(job_id=JOB_ID, selection=_selection(), evidence=evidence)
    second = builder.build(job_id=JOB_ID, selection=_selection(), evidence=evidence)

    assert first.pack.status.value == "ready"
    assert len(first.pack.metrics) == 10
    metrics = {metric.metric_name: metric for metric in first.pack.metrics}
    assert all(metric.status is MetricStatus.COMPUTED for metric in metrics.values())
    assert metrics["revenue_growth"].value == Decimal("0.25")
    assert metrics["free_cash_flow"].value == Decimal("10")
    assert all(metric.inputs_json["source_facts"] for metric in metrics.values())
    assert all(fact.accession_number != "target-original" for fact in first.pack.facts)
    assert second.reused is True

    store = ArtifactStore(tmp_path / str(JOB_ID))
    key = "annual/company-facts/selected.json"
    changed_ref = store.write(key, store.read(key) + b"\n", overwrite=True)
    assert evidence.company_facts is not None
    changed_evidence = evidence.model_copy(
        update={
            "company_facts": evidence.company_facts.model_copy(
                update={"selected_artifact": changed_ref}
            )
        }
    )
    rebuilt = builder.build(job_id=JOB_ID, selection=_selection(), evidence=changed_evidence)
    assert rebuilt.reused is False


def test_missing_canonical_fact_never_falls_back_to_original_and_unit_conflict_is_partial(
    tmp_path: Path,
) -> None:
    builder = AnnualComparisonBuilder(tmp_path)
    missing_canonical = builder.build(
        job_id=JOB_ID,
        selection=_selection(),
        evidence=_evidence(tmp_path, _facts(include_target_revenue=False)),
    )

    assert missing_canonical.pack.status.value == "partial"
    revenue_growth = next(
        metric
        for metric in missing_canonical.pack.metrics
        if metric.metric_name == "revenue_growth"
    )
    assert revenue_growth.status is MetricStatus.NOT_COMPUTABLE
    assert any("canonical" in limitation for limitation in missing_canonical.pack.limitations)

    conflicted_facts = _facts()
    conflicted_facts.append(
        _instant("AssetsCurrent", "70", year=2024, accession=TARGET_ACCESSION, unit="EUR")
    )
    conflicted = builder.build(
        job_id=UUID("00000000-0000-0000-0000-000000000707"),
        selection=_selection(),
        evidence=_evidence(
            tmp_path,
            conflicted_facts,
            job_id=UUID("00000000-0000-0000-0000-000000000707"),
        ),
    )
    current_ratio = next(
        metric for metric in conflicted.pack.metrics if metric.metric_name == "current_ratio"
    )
    assert conflicted.pack.status.value == "partial"
    assert current_ratio.status is MetricStatus.NOT_COMPUTABLE


def test_financial_only_uses_unique_comparator_facts_and_missing_base_blocks(
    tmp_path: Path,
) -> None:
    builder = AnnualComparisonBuilder(tmp_path)
    financial_only = builder.build(
        job_id=JOB_ID,
        selection=_selection(comparator=False),
        evidence=_evidence(tmp_path, _facts()),
    )
    blocked = builder.build(
        job_id=UUID("00000000-0000-0000-0000-000000000708"),
        selection=_selection(),
        evidence=_evidence(
            tmp_path,
            _facts(),
            job_id=UUID("00000000-0000-0000-0000-000000000708"),
            financial_ready=False,
        ),
    )

    assert financial_only.pack.status.value == "ready"
    assert financial_only.pack.comparator_accession_number == COMPARATOR_ACCESSION
    assert any("上一年度 10-K 缺失" in limitation for limitation in financial_only.pack.limitations)
    assert blocked.pack.status.value == "blocked"
    assert blocked.pack.metrics == ()


def test_52_53_week_fiscal_year_requires_exact_canonical_report_dates() -> None:
    def annual_revenue(
        value: str, *, fiscal_year: int, accession: str, start: date, end: date
    ) -> FinancialFact:
        return FinancialFact(
            company_id="0000789019",
            source_id="sec",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal(value),
            unit="USD",
            period_start=start,
            period_end=end,
            fiscal_year=fiscal_year,
            fiscal_period="FY",
            form_type="10-K",
            accession_number=accession,
        )

    calculation = compute_annual_comparison(
        [
            annual_revenue(
                "110",
                fiscal_year=2024,
                accession="fy24",
                start=date(2023, 6, 24),
                end=date(2024, 6, 28),
            ),
            annual_revenue(
                "100",
                fiscal_year=2023,
                accession="fy23",
                start=date(2022, 6, 26),
                end=date(2023, 6, 30),
            ),
        ],
        mapping=load_concept_mapping(CONCEPTS_V1_PATH),
        job_id="fixture",
        target_fiscal_year=2024,
        target_accession="fy24",
        target_report_date=date(2024, 6, 28),
        comparator_fiscal_year=2023,
        comparator_accession="fy23",
        comparator_report_date=date(2023, 6, 30),
    )

    assert calculation.metrics[0].status is MetricStatus.COMPUTED
    assert calculation.metrics[0].value == Decimal("0.1")
