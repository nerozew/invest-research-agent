"""P07-08：章节计划保持财务与叙事证据隔离。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from invest_research.domain.annual_evidence_routing import (
    AnnualAnalysisEvidenceBundle,
    AnnualEvidenceRoutingBundle,
    AnnualNarrativeEvidenceBundle,
    EvidenceRouteStatus,
)
from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
    ResearchDecisionAction,
)
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    SectionAnalysisPack,
    SectionDraft,
    SectionInputPack,
    SectionWorkStatus,
)
from invest_research.domain.models import AnnualComparisonInputFingerprint, AnnualComparisonPack
from invest_research.infrastructure.annual_section_planner import AnnualSectionPlanner


def _artifact(kind: EvidenceKind, *, year: int | None = None) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_key=f"annual/{kind.value}/{year or 'all'}.json",
        kind=kind,
        source_url="https://www.sec.gov/Archives/example",
        fiscal_year=year,
        content_checksum=f"checksum-{kind.value}-{year or 'all'}",
        validation_status=EvidenceValidationStatus.VALIDATED,
    )


def _comparison_pack() -> AnnualComparisonPack:
    company_facts = _artifact(EvidenceKind.COMPANY_FACTS)
    return AnnualComparisonPack(
        status=AnnualComparisonStatus.READY,
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        target_accession_number="target",
        comparator_accession_number="comparator",
        concept_mapping_version="concepts-v1",
        input_fingerprint=AnnualComparisonInputFingerprint(
            company_facts_artifact_key=company_facts.artifact_key,
            company_facts_checksum=company_facts.content_checksum,
            concept_mapping_version="concepts-v1",
        ),
    )


def _routing(*, partial: bool = False, include_event: bool = True) -> AnnualEvidenceRoutingBundle:
    financial = (_artifact(EvidenceKind.FINANCIAL_FACT_SET, year=2025),)
    narrative = [
        _artifact(EvidenceKind.TARGET_ANNUAL_FILING, year=2025),
        _artifact(EvidenceKind.COMPARATOR_ANNUAL_FILING, year=2024),
        _artifact(EvidenceKind.BUSINESS_OVERVIEW, year=2025),
        _artifact(EvidenceKind.RISK_FACTORS, year=2025),
        _artifact(EvidenceKind.MANAGEMENT_DISCUSSION, year=2024),
    ]
    if include_event:
        narrative.append(_artifact(EvidenceKind.MATERIAL_EVENT, year=2025))
    route_status = EvidenceRouteStatus.PARTIAL if partial else EvidenceRouteStatus.READY
    action = (
        ResearchDecisionAction.PARTIAL_READY
        if partial
        else ResearchDecisionAction.READY_FOR_ANALYSIS
    )
    if partial:
        narrative = [
            artifact
            for artifact in narrative
            if artifact.kind is not EvidenceKind.COMPARATOR_ANNUAL_FILING
        ]
    return AnnualEvidenceRoutingBundle(
        decision_action=action,
        analysis=AnnualAnalysisEvidenceBundle(
            status=EvidenceRouteStatus.READY,
            comparison_pack=_comparison_pack(),
            financial_fact_artifacts=financial,
        ),
        narrative=AnnualNarrativeEvidenceBundle(
            status=route_status,
            narrative_artifacts=tuple(narrative),
            limitations=("上一年度 10-K 不可用",) if partial else (),
        ),
    )


def _by_kind(plan: object) -> dict[AnnualSectionKind, SectionInputPack]:
    sections = getattr(plan, "sections")
    return {section.section: section for section in sections}


def test_complete_routing_creates_four_isolated_section_inputs() -> None:
    sections = _by_kind(AnnualSectionPlanner.build(_routing()))

    financial = sections[AnnualSectionKind.FINANCIAL_PERFORMANCE]
    assert financial.status is SectionWorkStatus.READY
    assert financial.comparison_pack is not None
    assert {artifact.kind for artifact in financial.evidence_artifacts} == {
        EvidenceKind.FINANCIAL_FACT_SET
    }
    assert sections[AnnualSectionKind.BUSINESS_OVERVIEW].status is SectionWorkStatus.READY
    assert sections[AnnualSectionKind.RISK_FACTORS].status is SectionWorkStatus.READY
    assert sections[AnnualSectionKind.MATERIAL_EVENTS].status is SectionWorkStatus.READY
    assert all(
        artifact.kind is not EvidenceKind.FINANCIAL_FACT_SET
        for section in sections.values()
        if section.section is not AnnualSectionKind.FINANCIAL_PERFORMANCE
        for artifact in section.evidence_artifacts
    )


def test_financial_only_keeps_financial_and_business_ready_but_limits_risk() -> None:
    sections = _by_kind(AnnualSectionPlanner.build(_routing(partial=True, include_event=False)))

    assert sections[AnnualSectionKind.FINANCIAL_PERFORMANCE].status is SectionWorkStatus.READY
    assert sections[AnnualSectionKind.BUSINESS_OVERVIEW].status is SectionWorkStatus.READY
    assert sections[AnnualSectionKind.RISK_FACTORS].status is SectionWorkStatus.PARTIAL
    assert any(
        "禁止风险与 MD&A 叙事同比" in item
        for item in sections[AnnualSectionKind.RISK_FACTORS].limitations
    )
    assert sections[AnnualSectionKind.MATERIAL_EVENTS].status is SectionWorkStatus.NOT_APPLICABLE


def test_blocked_routes_produce_only_blocked_section_inputs() -> None:
    routing = AnnualEvidenceRoutingBundle(
        decision_action=ResearchDecisionAction.BLOCKED,
        analysis=AnnualAnalysisEvidenceBundle(
            status=EvidenceRouteStatus.BLOCKED,
            limitations=("财务事实缺失",),
        ),
        narrative=AnnualNarrativeEvidenceBundle(
            status=EvidenceRouteStatus.BLOCKED,
            limitations=("目标 10-K 缺失",),
        ),
    )

    plan = AnnualSectionPlanner.build(routing)

    assert all(section.status is SectionWorkStatus.BLOCKED for section in plan.sections)
    assert all(not section.evidence_artifacts for section in plan.sections)


def test_input_contract_rejects_unvalidated_or_cross_consumer_evidence() -> None:
    unvalidated = _artifact(EvidenceKind.BUSINESS_OVERVIEW).model_copy(
        update={"validation_status": EvidenceValidationStatus.PRODUCED}
    )
    with pytest.raises(ValidationError):
        SectionInputPack(
            section=AnnualSectionKind.BUSINESS_OVERVIEW,
            status=SectionWorkStatus.READY,
            evidence_artifacts=(unvalidated,),
        )
    with pytest.raises(ValidationError):
        SectionInputPack(
            section=AnnualSectionKind.BUSINESS_OVERVIEW,
            status=SectionWorkStatus.READY,
            evidence_artifacts=(_artifact(EvidenceKind.FINANCIAL_FACT_SET, year=2025),),
            comparison_pack=_comparison_pack(),
        )


def test_financial_draft_requires_analysis_and_citations_stay_within_input_pack() -> None:
    financial_input = SectionInputPack(
        section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
        status=SectionWorkStatus.READY,
        evidence_artifacts=(_artifact(EvidenceKind.FINANCIAL_FACT_SET, year=2025),),
        comparison_pack=_comparison_pack(),
    )
    analysis = SectionAnalysisPack(
        section_input=financial_input,
        status=SectionWorkStatus.READY,
        analysis_notes="收入增长来自确定性比较包。",
        citation_artifact_keys=(next(iter(financial_input.allowed_artifact_keys)),),
    )
    draft = SectionDraft(
        section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
        status=SectionWorkStatus.READY,
        markdown="财务表现章节。",
        citation_artifact_keys=(next(iter(financial_input.allowed_artifact_keys)),),
        financial_analysis=analysis,
    )

    assert draft.financial_analysis is analysis
    with pytest.raises(ValidationError):
        SectionDraft(
            section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
            status=SectionWorkStatus.READY,
            markdown="越权草稿。",
            citation_artifact_keys=("annual/not-granted.json",),
            financial_analysis=analysis,
        )


def test_web_search_artifacts_route_to_narrative_sections_not_financial() -> None:
    """搜索证据工件（BUSINESS_OVERVIEW/RISK_FACTORS/MATERIAL_EVENT kind）进入对应叙事章节。"""
    narrative = [
        _artifact(EvidenceKind.BUSINESS_OVERVIEW),
        _artifact(EvidenceKind.RISK_FACTORS),
        _artifact(EvidenceKind.MATERIAL_EVENT),
    ]
    routing = AnnualEvidenceRoutingBundle(
        decision_action=ResearchDecisionAction.READY_FOR_ANALYSIS,
        analysis=AnnualAnalysisEvidenceBundle(
            status=EvidenceRouteStatus.READY,
            comparison_pack=_comparison_pack(),
            financial_fact_artifacts=(_artifact(EvidenceKind.FINANCIAL_FACT_SET, year=2025),),
        ),
        narrative=AnnualNarrativeEvidenceBundle(
            status=EvidenceRouteStatus.READY,
            narrative_artifacts=tuple(narrative),
        ),
    )

    sections = _by_kind(AnnualSectionPlanner.build(routing))
    assert {a.kind for a in sections[AnnualSectionKind.BUSINESS_OVERVIEW].evidence_artifacts} == {
        EvidenceKind.BUSINESS_OVERVIEW
    }
    assert {a.kind for a in sections[AnnualSectionKind.RISK_FACTORS].evidence_artifacts} == {
        EvidenceKind.RISK_FACTORS
    }
    assert {a.kind for a in sections[AnnualSectionKind.MATERIAL_EVENTS].evidence_artifacts} == {
        EvidenceKind.MATERIAL_EVENT
    }
    # 财务章节不消费任何搜索证据。
    financial = sections[AnnualSectionKind.FINANCIAL_PERFORMANCE]
    assert all(a.kind is EvidenceKind.FINANCIAL_FACT_SET for a in financial.evidence_artifacts)
