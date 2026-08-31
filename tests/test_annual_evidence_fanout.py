"""P07-04 年度证据 fan-out/fan-in 测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from threading import Barrier, Lock
from uuid import UUID

from invest_research.domain.annual_filing_selector import (
    AnnualFilingSelection,
    AnnualFilingSelectionStatus,
)
from invest_research.domain.annual_pipeline import (
    AnnualReadiness,
    CoverageRequirement,
    EvidenceKind,
)
from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import Filing
from invest_research.infrastructure.annual_company_facts_pipeline import (
    AnnualCompanyFactsArtifactResult,
    AnnualCompanyFactsStatus,
)
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualDocumentArtifactResult,
    AnnualDocumentFailure,
    AnnualDocumentPipelineStatus,
)
from invest_research.infrastructure.annual_evidence_fanout import AnnualEvidenceFanoutPipeline
from invest_research.infrastructure.annual_web_search_pipeline import (
    WebSearchArtifactResult,
    WebSearchEntry,
    WebSearchEvidenceStatus,
    WebSearchSectionEvidence,
)
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore

JOB_ID = UUID("00000000-0000-0000-0000-000000000705")


def _filing(year: int) -> Filing:
    return Filing(
        accession_number=f"0000000000-{year % 100:02d}-000001",
        form_type="10-K",
        filing_date=date(year + 1, 2, 1),
        report_period=date(year, 12, 31),
        primary_document_url=f"https://example.test/{year}.htm",
    )


def _ready_selection() -> AnnualFilingSelection:
    return AnnualFilingSelection(
        status=AnnualFilingSelectionStatus.READY,
        target_fiscal_year=2024,
        target_filing=_filing(2024),
        target_original_accession_number=_filing(2024).accession_number,
        comparator_fiscal_year=2023,
        comparator_filing=_filing(2023),
        comparator_original_accession_number=_filing(2023).accession_number,
    )


def _selected_filings(selection: AnnualFilingSelection) -> tuple[Filing, Filing]:
    assert selection.target_filing is not None
    assert selection.comparator_filing is not None
    return selection.target_filing, selection.comparator_filing


def _ref(key: str) -> ArtifactRef:
    return ArtifactRef(artifact_key=key, byte_size=1, content_checksum="a" * 64)


def _document_result(accession: str, *, failed: bool = False) -> AnnualDocumentArtifactResult:
    if failed:
        return AnnualDocumentArtifactResult(
            status=AnnualDocumentPipelineStatus.PARSE_FAILED,
            accession_number=accession,
            manifest_artifact=_ref(f"annual/{accession}/manifest.json"),
            failure=AnnualDocumentFailure(
                error_code=ErrorCode.DOCUMENT_UNSUPPORTED,
                message="parse failed",
                is_retryable=False,
            ),
        )
    return AnnualDocumentArtifactResult(
        status=AnnualDocumentPipelineStatus.COMPLETED,
        accession_number=accession,
        source_artifact=_ref(f"annual/{accession}/source.html"),
        parsed_artifact=_ref(f"annual/{accession}/parsed.json"),
        manifest_artifact=_ref(f"annual/{accession}/manifest.json"),
    )


def _facts_result(*years: int) -> AnnualCompanyFactsArtifactResult:
    return AnnualCompanyFactsArtifactResult(
        status=AnnualCompanyFactsStatus.COMPLETED,
        source_artifact=_ref("annual/company-facts/source.json"),
        selected_artifact=_ref("annual/company-facts/selected.json"),
        manifest_artifact=_ref("annual/company-facts/manifest.json"),
        available_fiscal_years=years,
    )


class StaticDocumentPipeline:
    def __init__(self, results: dict[str, AnnualDocumentArtifactResult]) -> None:
        self.results = results

    def run(self, *, job_id: UUID, filing: Filing) -> AnnualDocumentArtifactResult:
        assert job_id == JOB_ID
        return self.results[filing.accession_number]


class StaticFactsPipeline:
    def __init__(self, result: AnnualCompanyFactsArtifactResult) -> None:
        self.result = result

    def run(self, **_kwargs: object) -> AnnualCompanyFactsArtifactResult:
        return self.result


def _pipeline(
    *,
    target: AnnualDocumentArtifactResult | None = None,
    comparator: AnnualDocumentArtifactResult | None = None,
    facts: AnnualCompanyFactsArtifactResult | None = None,
) -> AnnualEvidenceFanoutPipeline:
    selection = _ready_selection()
    target_filing, comparator_filing = _selected_filings(selection)
    return AnnualEvidenceFanoutPipeline(
        StaticDocumentPipeline(
            {
                target_filing.accession_number: target
                or _document_result(target_filing.accession_number),
                comparator_filing.accession_number: comparator
                or _document_result(comparator_filing.accession_number),
            }
        ),
        StaticFactsPipeline(facts or _facts_result(2024, 2023)),
    )


def test_three_independent_tasks_start_before_fan_in() -> None:
    selection = _ready_selection()
    target_filing, comparator_filing = _selected_filings(selection)
    barrier = Barrier(3, timeout=2)
    started: list[str] = []
    lock = Lock()

    class BlockingDocumentPipeline(StaticDocumentPipeline):
        def run(self, *, job_id: UUID, filing: Filing) -> AnnualDocumentArtifactResult:
            with lock:
                started.append(filing.accession_number)
            barrier.wait()
            return super().run(job_id=job_id, filing=filing)

    class BlockingFactsPipeline(StaticFactsPipeline):
        def run(self, **kwargs: object) -> AnnualCompanyFactsArtifactResult:
            with lock:
                started.append("facts")
            barrier.wait()
            return super().run(**kwargs)

    pipeline = AnnualEvidenceFanoutPipeline(
        BlockingDocumentPipeline(
            {
                target_filing.accession_number: _document_result(target_filing.accession_number),
                comparator_filing.accession_number: _document_result(
                    comparator_filing.accession_number
                ),
            }
        ),
        BlockingFactsPipeline(_facts_result(2024, 2023)),
    )

    result = pipeline.run(
        job_id=JOB_ID, selection=selection, cik="0000789019", as_of_date=date(2025, 2, 15)
    )

    assert len(started) == 3
    assert result.coverage_ledger.readiness is AnnualReadiness.FULL_READY
    assert len(result.evidence_artifacts) == 5
    # WS3：工具调用统计落盘（2 份 10-K 下载 + 1 Company Facts；无 web pipeline 时不计数）。
    assert result.invocation_summary == {
        "filing_downloader_calls": 2,
        "sec_company_facts_calls": 1,
    }


def test_comparator_failure_allows_financial_only_and_missing_facts_blocks() -> None:
    selection = _ready_selection()
    _, comparator_filing = _selected_filings(selection)
    comparator_failed = _document_result(comparator_filing.accession_number, failed=True)
    financial_only = _pipeline(comparator=comparator_failed).run(
        job_id=JOB_ID, selection=selection, cik="0000789019", as_of_date=date(2025, 2, 15)
    )
    blocked = _pipeline(facts=_facts_result(2024)).run(
        job_id=JOB_ID, selection=selection, cik="0000789019", as_of_date=date(2025, 2, 15)
    )

    assert financial_only.coverage_ledger.readiness is AnnualReadiness.FINANCIAL_ONLY_READY
    assert financial_only.target_document is not None
    assert financial_only.target_document.status is AnnualDocumentPipelineStatus.COMPLETED
    assert financial_only.company_facts is not None
    assert financial_only.company_facts.status is AnnualCompanyFactsStatus.COMPLETED
    assert blocked.coverage_ledger.readiness is AnnualReadiness.BLOCKED


def test_missing_comparator_selection_does_not_dispatch_a_substitute_filing() -> None:
    target = _filing(2024)
    selection = AnnualFilingSelection(
        status=AnnualFilingSelectionStatus.COMPARATOR_MISSING,
        target_fiscal_year=2024,
        target_filing=target,
        target_original_accession_number=target.accession_number,
        comparator_fiscal_year=2023,
        missing_reason="no FY 2023 10-K",
    )
    pipeline = AnnualEvidenceFanoutPipeline(
        StaticDocumentPipeline(
            {target.accession_number: _document_result(target.accession_number)}
        ),
        StaticFactsPipeline(_facts_result(2024, 2023)),
    )

    result = pipeline.run(
        job_id=JOB_ID, selection=selection, cik="0000789019", as_of_date=date(2025, 2, 15)
    )

    assert result.comparator_document is None
    assert result.coverage_ledger.readiness is AnnualReadiness.FINANCIAL_ONLY_READY


def test_target_missing_returns_narrative_only_without_dispatching_tasks() -> None:
    selection = AnnualFilingSelection(
        status=AnnualFilingSelectionStatus.TARGET_MISSING,
        missing_reason="no target filing",
    )
    pipeline = _pipeline()

    result = pipeline.run(
        job_id=JOB_ID, selection=selection, cik="0000789019", as_of_date=date(2025, 2, 15)
    )

    assert result.coverage_ledger.readiness is AnnualReadiness.NARRATIVE_ONLY_READY


class StaticWebPipeline:
    def __init__(self, result: WebSearchArtifactResult) -> None:
        self.result = result

    def run(self, **_kwargs: object) -> WebSearchArtifactResult:
        return self.result


def test_web_search_fourth_route_adds_narrative_evidence_and_optional_coverage(
    tmp_path: Path,
) -> None:
    """第四路搜索证据进入叙事 EvidenceArtifact；覆盖账本增加可选 web 项且不影响 readiness。"""
    selection = _ready_selection()
    target_filing, comparator_filing = _selected_filings(selection)
    store = ArtifactStore(tmp_path / str(JOB_ID))
    section = WebSearchSectionEvidence(
        kind="business_overview",
        as_of_date="2025-02-15",
        entries=(
            WebSearchEntry(
                title="Reuters business",
                url="https://www.reuters.com/x",
                publisher="reuters.com",
                accessed_at=date(2025, 2, 15),
            ),
        ),
    )
    section_ref = store.write(
        "annual/web-search/business_overview.json",
        section.model_dump_json().encode("utf-8"),
        overwrite=True,
    )
    web_result = WebSearchArtifactResult(
        status=WebSearchEvidenceStatus.COMPLETED,
        section_artifacts={"business_overview": section_ref},
        manifest_artifact=_ref("annual/web-search/manifest.json"),
    )
    pipeline = AnnualEvidenceFanoutPipeline(
        StaticDocumentPipeline(
            {
                target_filing.accession_number: _document_result(target_filing.accession_number),
                comparator_filing.accession_number: _document_result(
                    comparator_filing.accession_number
                ),
            }
        ),
        StaticFactsPipeline(_facts_result(2024, 2023)),
        web_search_pipeline=StaticWebPipeline(web_result),
        artifact_root=tmp_path,
    )

    result = pipeline.run(
        job_id=JOB_ID,
        selection=selection,
        cik="0000789019",
        as_of_date=date(2025, 2, 15),
        company="MSFT",
    )

    assert result.web_search is not None
    assert EvidenceKind.BUSINESS_OVERVIEW in {a.kind for a in result.evidence_artifacts}
    requirements = {item.requirement for item in result.coverage_ledger.items}
    assert CoverageRequirement.CURRENT_BUSINESS_OVERVIEW in requirements
    # 核心 readiness 仍由 SEC 证据决定，搜索增强不改变它。
    assert result.coverage_ledger.readiness is AnnualReadiness.FULL_READY


def test_web_search_not_configured_keeps_legacy_ledger_and_evidence() -> None:
    """未注入 web_search_pipeline 时行为与旧版完全一致（无 web 证据、无 web 覆盖项）。"""
    selection = _ready_selection()
    pipeline = _pipeline()
    result = pipeline.run(
        job_id=JOB_ID, selection=selection, cik="0000789019", as_of_date=date(2025, 2, 15)
    )
    assert result.web_search is None
    web_kinds = {
        EvidenceKind.BUSINESS_OVERVIEW,
        EvidenceKind.RISK_FACTORS,
        EvidenceKind.MANAGEMENT_DISCUSSION,
        EvidenceKind.MATERIAL_EVENT,
    }
    assert not any(a.kind in web_kinds for a in result.evidence_artifacts)
    web_requirements = {
        CoverageRequirement.CURRENT_BUSINESS_OVERVIEW,
        CoverageRequirement.CURRENT_RISK_FACTORS,
        CoverageRequirement.COMPARATOR_MANAGEMENT_DISCUSSION,
    }
    assert not any(
        item.requirement in web_requirements for item in result.coverage_ledger.items
    )
