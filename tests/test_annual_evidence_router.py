"""P07-07：年度证据路由不泄露未验证或职责不匹配的工件。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from invest_research.domain.annual_evidence_routing import (
    AnnualNarrativeEvidenceBundle,
    EvidenceRouteStatus,
)
from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    AnnualReadiness,
    CoverageItem,
    CoverageLedger,
    CoverageRequirement,
    CoverageStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
    ResearchDecision,
    ResearchDecisionAction,
    ResearchNodeKind,
    SupplementRequest,
)
from invest_research.domain.models import AnnualComparisonInputFingerprint, AnnualComparisonPack
from invest_research.infrastructure.annual_evidence_fanout import AnnualEvidenceBundle
from invest_research.infrastructure.annual_evidence_router import AnnualEvidenceRouter


def _artifact(
    kind: EvidenceKind,
    *,
    fiscal_year: int | None = None,
    validation_status: EvidenceValidationStatus = EvidenceValidationStatus.VALIDATED,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_key=f"annual/{kind.value}/{fiscal_year or 'all'}.json",
        kind=kind,
        source_url="https://www.sec.gov/Archives/example",
        fiscal_year=fiscal_year,
        content_checksum=f"sha256-{kind.value}-{fiscal_year or 'all'}",
        validation_status=validation_status,
    )


def _ledger(*, comparator: bool = True) -> CoverageLedger:
    def item(requirement: CoverageRequirement, satisfied: bool = True) -> CoverageItem:
        return CoverageItem(
            requirement=requirement,
            status=CoverageStatus.SATISFIED if satisfied else CoverageStatus.MISSING,
            evidence_artifact_keys=(requirement.value,) if satisfied else (),
            missing_reason=None if satisfied else "上一年度 10-K 不可用",
        )

    return CoverageLedger(
        target_fiscal_year=2025,
        readiness=(
            AnnualReadiness.FULL_READY if comparator else AnnualReadiness.FINANCIAL_ONLY_READY
        ),
        items=(
            item(CoverageRequirement.TARGET_ANNUAL_FILING),
            item(CoverageRequirement.TARGET_FINANCIAL_FACTS),
            item(CoverageRequirement.COMPARATOR_FINANCIAL_FACTS),
            item(CoverageRequirement.COMPARATOR_ANNUAL_FILING, comparator),
        ),
    )


def _evidence(
    *, comparator: bool = True, invalid: EvidenceArtifact | None = None
) -> AnnualEvidenceBundle:
    artifacts = [
        _artifact(EvidenceKind.TARGET_ANNUAL_FILING, fiscal_year=2025),
        _artifact(EvidenceKind.COMPANY_FACTS),
        _artifact(EvidenceKind.FINANCIAL_FACT_SET, fiscal_year=2025),
        _artifact(EvidenceKind.FINANCIAL_FACT_SET, fiscal_year=2024),
        _artifact(EvidenceKind.BUSINESS_OVERVIEW, fiscal_year=2025),
        _artifact(EvidenceKind.RISK_FACTORS, fiscal_year=2025),
        _artifact(EvidenceKind.MANAGEMENT_DISCUSSION, fiscal_year=2024),
        _artifact(EvidenceKind.MATERIAL_EVENT, fiscal_year=2025),
    ]
    if comparator:
        artifacts.append(_artifact(EvidenceKind.COMPARATOR_ANNUAL_FILING, fiscal_year=2024))
    if invalid is not None:
        artifacts.append(invalid)
    return AnnualEvidenceBundle(
        evidence_artifacts=tuple(artifacts),
        coverage_ledger=_ledger(comparator=comparator),
    )


def _pack(
    *,
    status: AnnualComparisonStatus = AnnualComparisonStatus.READY,
    checksum: str | None = None,
) -> AnnualComparisonPack:
    facts_artifact = _artifact(EvidenceKind.COMPANY_FACTS)
    return AnnualComparisonPack(
        status=status,
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        target_accession_number="target-accession",
        comparator_accession_number="comparator-accession",
        concept_mapping_version="concepts-v1",
        input_fingerprint=AnnualComparisonInputFingerprint(
            company_facts_artifact_key=facts_artifact.artifact_key,
            company_facts_checksum=checksum or facts_artifact.content_checksum,
            concept_mapping_version="concepts-v1",
        ),
        limitations=("比较基础证据不可用",)
        if status is AnnualComparisonStatus.BLOCKED
        else ("部分指标不可计算",)
        if status is AnnualComparisonStatus.PARTIAL
        else (),
    )


def _decision(action: ResearchDecisionAction) -> ResearchDecision:
    return ResearchDecision(action=action, reason="fixture decision")


def test_routes_financial_and_narrative_evidence_to_separate_consumers() -> None:
    result = AnnualEvidenceRouter.route(
        evidence=_evidence(),
        comparison_pack=_pack(),
        decision=_decision(ResearchDecisionAction.READY_FOR_ANALYSIS),
    )

    assert result.analysis.status is EvidenceRouteStatus.READY
    assert result.analysis.comparison_pack is not None
    assert {artifact.kind for artifact in result.analysis.financial_fact_artifacts} == {
        EvidenceKind.FINANCIAL_FACT_SET
    }
    assert result.narrative.status is EvidenceRouteStatus.READY
    assert EvidenceKind.COMPANY_FACTS not in {
        artifact.kind for artifact in result.narrative.narrative_artifacts
    }
    assert EvidenceKind.FINANCIAL_FACT_SET not in {
        artifact.kind for artifact in result.narrative.narrative_artifacts
    }
    assert {
        EvidenceKind.BUSINESS_OVERVIEW,
        EvidenceKind.RISK_FACTORS,
        EvidenceKind.MANAGEMENT_DISCUSSION,
        EvidenceKind.MATERIAL_EVENT,
    }.issubset({artifact.kind for artifact in result.narrative.narrative_artifacts})


def test_financial_only_routes_analysis_but_hides_comparator_narrative_evidence() -> None:
    result = AnnualEvidenceRouter.route(
        evidence=_evidence(comparator=False),
        comparison_pack=_pack(),
        decision=_decision(ResearchDecisionAction.PARTIAL_READY),
    )

    assert result.analysis.status is EvidenceRouteStatus.READY
    assert result.narrative.status is EvidenceRouteStatus.PARTIAL
    assert all(
        artifact.kind is not EvidenceKind.COMPARATOR_ANNUAL_FILING
        for artifact in result.narrative.narrative_artifacts
    )
    assert "上一年度 10-K 不可用" in result.narrative.limitations


def test_blocked_and_continue_search_expose_no_artifacts() -> None:
    continue_decision = ResearchDecision(
        action=ResearchDecisionAction.CONTINUE_SEARCH,
        reason="仍需补证",
        gap_requirements=(CoverageRequirement.TARGET_ANNUAL_FILING,),
        requested_node_kinds=(ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,),
        supplement_requests=(
            SupplementRequest(
                requirement=CoverageRequirement.TARGET_ANNUAL_FILING,
                node_kind=ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,
            ),
        ),
    )
    for decision in (_decision(ResearchDecisionAction.BLOCKED), continue_decision):
        result = AnnualEvidenceRouter.route(
            evidence=_evidence(), comparison_pack=_pack(), decision=decision
        )

        assert result.analysis.status is EvidenceRouteStatus.BLOCKED
        assert result.narrative.status is EvidenceRouteStatus.BLOCKED
        assert not result.analysis.financial_fact_artifacts
        assert not result.narrative.narrative_artifacts


def test_unvalidated_evidence_and_checksum_mismatch_are_never_routed() -> None:
    result = AnnualEvidenceRouter.route(
        evidence=_evidence(
            invalid=_artifact(
                EvidenceKind.FINANCIAL_FACT_SET,
                fiscal_year=2025,
                validation_status=EvidenceValidationStatus.PRODUCED,
            )
        ),
        comparison_pack=_pack(checksum="checksum-mismatch"),
        decision=_decision(ResearchDecisionAction.READY_FOR_ANALYSIS),
    )

    assert result.analysis.status is EvidenceRouteStatus.BLOCKED
    assert result.analysis.comparison_pack is None
    assert all(
        artifact.validation_status is EvidenceValidationStatus.VALIDATED
        for artifact in result.narrative.narrative_artifacts
    )
    assert any("checksum" in limitation for limitation in result.analysis.limitations)


def test_blocked_comparison_pack_is_not_routed_to_analysis() -> None:
    result = AnnualEvidenceRouter.route(
        evidence=_evidence(),
        comparison_pack=_pack(status=AnnualComparisonStatus.BLOCKED),
        decision=_decision(ResearchDecisionAction.READY_FOR_ANALYSIS),
    )

    assert result.analysis.status is EvidenceRouteStatus.BLOCKED
    assert result.analysis.comparison_pack is None


def test_writer_contract_rejects_company_facts_even_when_validated() -> None:
    with pytest.raises(ValidationError):
        AnnualNarrativeEvidenceBundle(
            status=EvidenceRouteStatus.READY,
            narrative_artifacts=(_artifact(EvidenceKind.COMPANY_FACTS),),
        )
