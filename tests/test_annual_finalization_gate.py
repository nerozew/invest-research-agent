"""P07-09：年度最终门禁只汇总完整核心章节，并限制修订次数。"""

from __future__ import annotations

from invest_research.application.annual_finalization_gate import AnnualFinalizationGate
from invest_research.domain.annual_finalization import AnnualFinalizationStatus
from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
)
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    SectionAnalysisPack,
    SectionDraft,
    SectionInputPack,
    SectionWorkStatus,
)
from invest_research.domain.models import AnnualComparisonInputFingerprint, AnnualComparisonPack


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
    facts = _artifact(EvidenceKind.COMPANY_FACTS)
    return AnnualComparisonPack(
        status=AnnualComparisonStatus.READY,
        target_fiscal_year=2025,
        comparator_fiscal_year=2024,
        target_accession_number="target",
        comparator_accession_number="comparator",
        concept_mapping_version="concepts-v1",
        input_fingerprint=AnnualComparisonInputFingerprint(
            company_facts_artifact_key=facts.artifact_key,
            company_facts_checksum=facts.content_checksum,
            concept_mapping_version="concepts-v1",
        ),
    )


def _financial_draft(*, partial: bool = False, cite: bool = True) -> SectionDraft:
    status = SectionWorkStatus.PARTIAL if partial else SectionWorkStatus.READY
    fact = _artifact(EvidenceKind.FINANCIAL_FACT_SET, year=2025)
    section_input = SectionInputPack(
        section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
        status=status,
        evidence_artifacts=(fact,),
        comparison_pack=_comparison_pack(),
        limitations=("一个指标不可计算",) if partial else (),
    )
    citations = (fact.artifact_key,) if cite else ()
    analysis = SectionAnalysisPack(
        section_input=section_input,
        status=status,
        analysis_notes="确定性比较结果。",
        citation_artifact_keys=citations,
        limitations=("一个指标不可计算",) if partial else (),
    )
    return SectionDraft(
        section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
        status=status,
        markdown="财务表现章节。",
        citation_artifact_keys=citations,
        financial_analysis=analysis,
        limitations=("一个指标不可计算",) if partial else (),
    )


def _narrative_draft(
    section: AnnualSectionKind,
    *,
    status: SectionWorkStatus = SectionWorkStatus.READY,
    cite: bool = True,
) -> SectionDraft:
    kinds = {
        AnnualSectionKind.BUSINESS_OVERVIEW: (EvidenceKind.TARGET_ANNUAL_FILING,),
        AnnualSectionKind.RISK_FACTORS: (EvidenceKind.RISK_FACTORS,),
        AnnualSectionKind.MATERIAL_EVENTS: (EvidenceKind.MATERIAL_EVENT,),
    }
    if status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
        section_input = SectionInputPack(
            section=section,
            status=status,
            limitations=("章节没有可用证据",),
        )
        return SectionDraft(
            section=section,
            status=status,
            section_input=section_input,
            limitations=("章节没有可用证据",),
        )
    artifact = _artifact(kinds[section][0], year=2025)
    citations = (artifact.artifact_key,) if cite else ()
    section_input = SectionInputPack(
        section=section,
        status=status,
        evidence_artifacts=(artifact,),
        limitations=("叙事同比不可用",) if status is SectionWorkStatus.PARTIAL else (),
    )
    return SectionDraft(
        section=section,
        status=status,
        markdown=f"{section.value} 章节。",
        citation_artifact_keys=citations,
        section_input=section_input,
        limitations=("叙事同比不可用",) if status is SectionWorkStatus.PARTIAL else (),
    )


def _drafts(
    *,
    financial_partial: bool = False,
    event_status: SectionWorkStatus = SectionWorkStatus.READY,
    business_cite: bool = True,
    risk_status: SectionWorkStatus = SectionWorkStatus.READY,
) -> tuple[SectionDraft, ...]:
    return (
        _financial_draft(partial=financial_partial),
        _narrative_draft(AnnualSectionKind.BUSINESS_OVERVIEW, cite=business_cite),
        _narrative_draft(AnnualSectionKind.RISK_FACTORS, status=risk_status),
        _narrative_draft(AnnualSectionKind.MATERIAL_EVENTS, status=event_status),
    )


def test_complete_sections_build_final_writer_pack_in_stable_order() -> None:
    decision = AnnualFinalizationGate.evaluate(_drafts())

    assert decision.status is AnnualFinalizationStatus.READY_FOR_FINAL_WRITER
    assert decision.finalization_pack is not None
    assert [section.section for section in decision.finalization_pack.sections] == [
        AnnualSectionKind.FINANCIAL_PERFORMANCE,
        AnnualSectionKind.BUSINESS_OVERVIEW,
        AnnualSectionKind.RISK_FACTORS,
        AnnualSectionKind.MATERIAL_EVENTS,
    ]
    assert len(decision.finalization_pack.allowed_citation_artifact_keys) == 4


def test_partial_core_and_not_applicable_event_are_safe_to_finalize() -> None:
    decision = AnnualFinalizationGate.evaluate(
        _drafts(financial_partial=True, event_status=SectionWorkStatus.NOT_APPLICABLE)
    )

    assert decision.status is AnnualFinalizationStatus.PARTIAL_READY_FOR_FINAL_WRITER
    assert decision.finalization_pack is not None
    assert "一个指标不可计算" in decision.finalization_pack.limitations
    event = decision.finalization_pack.sections[-1]
    assert event.status is SectionWorkStatus.NOT_APPLICABLE
    assert event.markdown is None


def test_blocked_core_section_rejects_finalization_without_exposing_pack() -> None:
    decision = AnnualFinalizationGate.evaluate(
        _drafts(risk_status=SectionWorkStatus.BLOCKED)
    )

    assert decision.status is AnnualFinalizationStatus.BLOCKED
    assert decision.finalization_pack is None
    assert decision.revision_request is None
    assert decision.issues[0].code == "core_section_unavailable"


def test_missing_core_citation_requests_one_targeted_revision_then_blocks() -> None:
    first = AnnualFinalizationGate.evaluate(_drafts(business_cite=False))
    second = AnnualFinalizationGate.evaluate(_drafts(business_cite=False), revision_attempt=1)

    assert first.status is AnnualFinalizationStatus.NEEDS_SECTION_REVISION
    assert first.revision_request is not None
    assert first.revision_request.revision_number == 1
    assert first.revision_request.target_sections == (AnnualSectionKind.BUSINESS_OVERVIEW,)
    assert second.status is AnnualFinalizationStatus.BLOCKED
    assert {issue.code for issue in second.issues} == {
        "missing_section_citation",
        "section_revision_exhausted",
    }
