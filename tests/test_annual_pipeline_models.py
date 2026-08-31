"""P07-01：年度管线领域契约的纯模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from invest_research.domain.annual_pipeline import (
    AnnualReadiness,
    CoverageItem,
    CoverageLedger,
    CoverageRequirement,
    CoverageStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
    NodeDependency,
    ResearchDecision,
    ResearchDecisionAction,
    ResearchDecisionReasonCode,
    ResearchMode,
    ResearchNode,
    ResearchNodeKind,
    ResearchNodeStatus,
    SupplementRequest,
)
from invest_research.domain.models import ResearchRequest


def _satisfied(requirement: CoverageRequirement, key: str) -> CoverageItem:
    return CoverageItem(
        requirement=requirement,
        status=CoverageStatus.SATISFIED,
        evidence_artifact_keys=(key,),
    )


def test_mode_and_node_status_values_are_stable() -> None:
    assert ResearchMode.LEGACY.value == "legacy"
    assert ResearchMode.ANNUAL_DEEP.value == "annual_deep"
    assert ResearchNodeStatus.BLOCKED.value == "blocked"
    assert ResearchNodeStatus.BLOCKED.is_terminal is True
    assert ResearchNodeStatus.FAILED_RETRYABLE.is_terminal is False


def test_research_request_keeps_legacy_fingerprint_compatible() -> None:
    request = ResearchRequest(input_company="Microsoft", as_of_date="2025-01-01")

    assert request.research_mode is ResearchMode.LEGACY
    assert request.idempotency_fingerprint() == (
        '{"input_company":"Microsoft","as_of_date":"2025-01-01",'
        '"language":"zh-CN","requested_forms":["10-K","10-Q"],'
        '"research_profile":"deep"}'
    )


def test_annual_mode_is_part_of_request_fingerprint() -> None:
    legacy = ResearchRequest(input_company="Microsoft", as_of_date="2025-01-01")
    annual = legacy.model_copy(update={"research_mode": ResearchMode.ANNUAL_DEEP})

    assert legacy.idempotency_fingerprint() != annual.idempotency_fingerprint()
    assert '"research_mode":"annual_deep"' in annual.idempotency_fingerprint()


def test_node_dependency_rejects_self_reference_and_blocked_reason_is_required() -> None:
    with pytest.raises(ValidationError):
        NodeDependency(upstream_node_key="parse", downstream_node_key="parse")
    with pytest.raises(ValidationError):
        ResearchNode(
            node_key="blocked-parse",
            kind=ResearchNodeKind.PARSE_FILING,
            status=ResearchNodeStatus.BLOCKED,
        )

    node = ResearchNode(
        node_key="parse-target-10k",
        kind=ResearchNodeKind.PARSE_FILING,
        status=ResearchNodeStatus.BLOCKED,
        blocked_reason="目标 filing 不可访问",
    )
    assert ResearchNode.model_validate_json(node.model_dump_json()) == node


def test_evidence_artifact_roundtrip_and_invalid_status_rejected() -> None:
    artifact = EvidenceArtifact(
        artifact_key="annual/2025/10-k-business.json",
        kind=EvidenceKind.BUSINESS_OVERVIEW,
        source_url="https://www.sec.gov/Archives/example",
        locator="Item 1",
        fiscal_year=2025,
        content_checksum="sha256:abc",
        parser_version="sec-html-v1",
        validation_status=EvidenceValidationStatus.VALIDATED,
    )

    assert EvidenceArtifact.model_validate_json(artifact.model_dump_json()) == artifact
    with pytest.raises(ValidationError):
        EvidenceArtifact(
            artifact_key="x",
            kind="untrusted_note",
            source_url="https://example.com",
            content_checksum="x",
        )


def test_full_ready_requires_target_and_comparator_annual_evidence() -> None:
    ledger = CoverageLedger(
        target_fiscal_year=2025,
        readiness=AnnualReadiness.FULL_READY,
        items=(
            _satisfied(CoverageRequirement.TARGET_ANNUAL_FILING, "10-k-2025"),
            _satisfied(CoverageRequirement.TARGET_FINANCIAL_FACTS, "facts-2025"),
            _satisfied(CoverageRequirement.COMPARATOR_FINANCIAL_FACTS, "facts-2024"),
            _satisfied(CoverageRequirement.COMPARATOR_ANNUAL_FILING, "10-k-2024"),
        ),
    )

    assert CoverageLedger.model_validate_json(ledger.model_dump_json()) == ledger


def test_comparator_10k_missing_allows_financial_only_with_reason() -> None:
    ledger = CoverageLedger(
        target_fiscal_year=2025,
        readiness=AnnualReadiness.FINANCIAL_ONLY_READY,
        items=(
            _satisfied(CoverageRequirement.TARGET_ANNUAL_FILING, "10-k-2025"),
            _satisfied(CoverageRequirement.TARGET_FINANCIAL_FACTS, "facts-2025"),
            _satisfied(CoverageRequirement.COMPARATOR_FINANCIAL_FACTS, "facts-2024"),
            CoverageItem(
                requirement=CoverageRequirement.COMPARATOR_ANNUAL_FILING,
                status=CoverageStatus.MISSING,
                missing_reason="FY 2024 10-K 不在截至日期前的 submissions 中",
            ),
        ),
    )

    assert ledger.readiness is AnnualReadiness.FINANCIAL_ONLY_READY


def test_missing_target_10k_allows_only_narrative_ready_with_reason() -> None:
    ledger = CoverageLedger(
        target_fiscal_year=None,
        readiness=AnnualReadiness.NARRATIVE_ONLY_READY,
        items=(
            CoverageItem(
                requirement=CoverageRequirement.TARGET_ANNUAL_FILING,
                status=CoverageStatus.MISSING,
                missing_reason="截至日期前无可用 10-K",
            ),
            _satisfied(CoverageRequirement.CURRENT_BUSINESS_OVERVIEW, "business-current"),
        ),
    )

    assert ledger.readiness is AnnualReadiness.NARRATIVE_ONLY_READY


def test_continue_search_requires_a_gap_and_a_requested_node() -> None:
    with pytest.raises(ValidationError):
        ResearchDecision(
            action=ResearchDecisionAction.CONTINUE_SEARCH,
            reason="仍缺少上一年度 filing",
        )

    decision = ResearchDecision(
        action=ResearchDecisionAction.CONTINUE_SEARCH,
        reason="仍缺少上一年度 filing",
        gap_requirements=(CoverageRequirement.COMPARATOR_ANNUAL_FILING,),
        requested_node_kinds=(ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,),
        supplement_requests=(
            SupplementRequest(
                requirement=CoverageRequirement.COMPARATOR_ANNUAL_FILING,
                target_fiscal_year=2025,
                node_kind=ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,
            ),
        ),
    )
    assert ResearchDecision.model_validate_json(decision.model_dump_json()) == decision
    assert decision.reason_code is ResearchDecisionReasonCode.SUPPLEMENT_REQUIRED
