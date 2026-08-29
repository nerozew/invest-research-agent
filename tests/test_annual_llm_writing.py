"""P07-10B annual LLM writing access-control tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from invest_research.agents.llm_factory import LLMConfig, LLMRole
from invest_research.application.analysis_assembler import build_fact_ref
from invest_research.application.report_draft_assembler import build_source_citation_key
from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
)
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    SectionInputPack,
    SectionWorkStatus,
)
from invest_research.domain.models import (
    AnnualComparisonInputFingerprint,
    AnnualComparisonPack,
    FinancialFact,
)
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualParsedDocument,
    AnnualParsedTextBlock,
)
from invest_research.infrastructure.annual_llm_writing import (
    AnnualLlmCallError,
    AnnualLlmDispatcher,
    AnnualLlmResult,
    AnnualSectionExecutor,
)
from invest_research.infrastructure.annual_web_search_pipeline import (
    WebSearchEntry,
    WebSearchSectionEvidence,
)
from invest_research.tools.artifact_store import ArtifactStore


class _Completion:
    def __init__(self, responder: object) -> None:
        self._responder = responder
        self.calls: list[dict[str, str]] = []

    def complete(
        self, *, role: object, system_prompt: str, user_prompt: str, **_: object
    ) -> AnnualLlmResult:
        self.calls.append({"role": str(role), "system": system_prompt, "user": user_prompt})
        return self._responder(user_prompt)  # type: ignore[operator]


def test_annual_dispatcher_records_real_usage_without_prompt_content() -> None:
    class _Completions:
        def create(self, **_: object) -> object:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="valid"), finish_reason="stop"
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=12,
                    completion_tokens=8,
                    total_tokens=20,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=3),
                ),
            )

    config = LLMConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-annual-test-key",
        model_research="research-model",
        model_analysis="analysis-model",
        model_writer="writer-model",
    )
    dispatcher = AnnualLlmDispatcher(
        config,
        client_factory=lambda _: SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
    )

    result = dispatcher.complete(
        role=LLMRole.ANALYSIS,
        system_prompt="system secret context",
        user_prompt="user secret context",
    )

    assert result.total_tokens == 20
    assert dispatcher.performance_summary()["token_usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "cached_prompt_tokens": 3,
        "total_tokens": 20,
    }
    assert "system secret context" not in str(dispatcher.requests)
    assert "user secret context" not in str(dispatcher.requests)


def _artifact(key: str, checksum: str, *, kind: EvidenceKind) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_key=key,
        kind=kind,
        source_url="https://www.sec.gov/Archives/example-10k.htm",
        content_checksum=checksum,
        validation_status=EvidenceValidationStatus.VALIDATED,
    )


def test_narrative_writer_reads_only_authorized_parsed_artifact(tmp_path: Path) -> None:
    job_id = "job-1"
    parsed = AnnualParsedDocument(
        source_checksum="source-checksum",
        media_type="text/html",
        parser_name="html",
        blocks=(
            AnnualParsedTextBlock(text="Business secret product detail", locator="offset:1"),
            AnnualParsedTextBlock(text="Risk factors should not be selected", locator="offset:2"),
        ),
    )
    ref = ArtifactStore(tmp_path / job_id).write(
        "annual/target/parsed.json", parsed.model_dump_json().encode()
    )
    artifact = _artifact(
        ref.artifact_key, ref.content_checksum, kind=EvidenceKind.TARGET_ANNUAL_FILING
    )
    pack = SectionInputPack(
        section=AnnualSectionKind.BUSINESS_OVERVIEW,
        status=SectionWorkStatus.READY,
        evidence_artifacts=(artifact,),
    )
    citation = build_source_citation_key(
        type("Source", (), {"canonical_url": artifact.source_url})()
    )
    completion = _Completion(
        lambda _: AnnualLlmResult(
            markdown=(
                "## Business overview\n"
                "The disclosed product detail supports this limited business description. "
                f"No external information is used. [{citation}]"
            )
        )
    )

    draft = AnnualSectionExecutor(tmp_path, completion).write_narrative(job_id=job_id, pack=pack)

    assert draft.citation_artifact_keys == (artifact.artifact_key,)
    prompt = completion.calls[0]["user"]
    assert "Business secret product detail" in prompt
    assert "Company Facts" not in prompt
    assert "Risk factors should not be selected" not in prompt


def test_narrative_writer_rejects_unauthorized_citation(tmp_path: Path) -> None:
    job_id = "job-2"
    parsed = AnnualParsedDocument(
        source_checksum="source-checksum",
        media_type="text/html",
        parser_name="html",
        blocks=(AnnualParsedTextBlock(text="Business overview", locator="offset:1"),),
    )
    ref = ArtifactStore(tmp_path / job_id).write(
        "annual/target/parsed.json", parsed.model_dump_json().encode()
    )
    pack = SectionInputPack(
        section=AnnualSectionKind.BUSINESS_OVERVIEW,
        status=SectionWorkStatus.READY,
        evidence_artifacts=(
            _artifact(
                ref.artifact_key,
                ref.content_checksum,
                kind=EvidenceKind.TARGET_ANNUAL_FILING,
            ),
        ),
    )
    completion = _Completion(
        lambda _: AnnualLlmResult(
            markdown=(
                "## Business overview\nThis statement intentionally cites an unauthorized "
                "source key and is long enough for output validation. [src_not_authorized]"
            )
        )
    )

    with pytest.raises(AnnualLlmCallError) as error:
        AnnualSectionExecutor(tmp_path, completion).write_narrative(job_id=job_id, pack=pack)
    assert error.value.code == "ANNUAL_LLM_CITATION_UNAUTHORIZED"


def test_financial_analysis_only_receives_deterministic_facts(tmp_path: Path) -> None:
    fact = FinancialFact(
        company_id="0000000001",
        source_id="sec-facts",
        taxonomy="us-gaap",
        concept="Revenues",
        value=Decimal("100"),
        unit="USD",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        fiscal_year=2024,
        fiscal_period="FY",
        form_type="10-K",
        accession_number="0001",
    )
    artifact = _artifact(
        "annual/company-facts/selected.json", "a" * 64, kind=EvidenceKind.FINANCIAL_FACT_SET
    )
    comparison = AnnualComparisonPack(
        status=AnnualComparisonStatus.READY,
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
        target_accession_number="0001",
        comparator_accession_number="0000",
        concept_mapping_version="v1",
        input_fingerprint=AnnualComparisonInputFingerprint(
            company_facts_artifact_key=artifact.artifact_key,
            company_facts_checksum=artifact.content_checksum,
            concept_mapping_version="v1",
        ),
        facts=(fact,),
    )
    pack = SectionInputPack(
        section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
        status=SectionWorkStatus.READY,
        evidence_artifacts=(artifact,),
        comparison_pack=comparison,
    )
    ref = build_fact_ref(fact)
    completion = _Completion(
        lambda _: AnnualLlmResult(
            markdown=(
                "Revenue is described only as a deterministic SEC fact. No arithmetic or external "
                f"claim is introduced in this analysis. [{ref}]"
            )
        )
    )

    analysis = AnnualSectionExecutor(tmp_path, completion).analyze_financial(pack)

    assert analysis.citation_artifact_keys == (artifact.artifact_key,)
    prompt = completion.calls[0]["user"]
    assert "Revenues" in prompt
    assert "annual/target/parsed.json" not in prompt


def test_narrative_writer_consumes_web_search_evidence_with_per_entry_citations(
    tmp_path: Path,
) -> None:
    """网页搜索证据进叙事上下文：每条结果独立 [src_] 引用 + 可信度边界标注。"""
    job_id = "job-web"
    section = WebSearchSectionEvidence(
        kind="business_overview",
        as_of_date="2026-08-26",
        entries=(
            WebSearchEntry(
                title="MSFT launches new AI product",
                url="https://news.example.com/msft-ai",
                publisher="news.example.com",
                accessed_at=date(2026, 8, 26),
                snippet="Microsoft announced a new AI service.",
            ),
            WebSearchEntry(
                title="MSFT earnings call guidance",
                url="https://finance.example.com/msft-guidance",
                publisher="finance.example.com",
                accessed_at=date(2026, 8, 26),
                snippet="Management guided to strong cloud growth.",
            ),
        ),
        note="管理层/第三方观点，非 SEC 官方申报",
    )
    store = ArtifactStore(tmp_path / job_id)
    ref = store.write(
        "annual/web-search/business_overview.json", section.model_dump_json().encode()
    )
    artifact = EvidenceArtifact(
        artifact_key=ref.artifact_key,
        kind=EvidenceKind.BUSINESS_OVERVIEW,
        source_url="https://news.example.com/msft-ai",
        content_checksum=ref.content_checksum,
        parser_version="annual_web_search_section_v1",
        validation_status=EvidenceValidationStatus.VALIDATED,
    )
    pack = SectionInputPack(
        section=AnnualSectionKind.BUSINESS_OVERVIEW,
        status=SectionWorkStatus.READY,
        evidence_artifacts=(artifact,),
    )
    citation_1 = build_source_citation_key(
        type("Source", (), {"canonical_url": section.entries[0].url})()
    )
    citation_2 = build_source_citation_key(
        type("Source", (), {"canonical_url": section.entries[1].url})()
    )
    completion = _Completion(
        lambda _: AnnualLlmResult(
            markdown=(
                "## Business overview\n"
                f"MSFT launched a new AI product [{citation_1}]. "
                f"Management guided to cloud growth [{citation_2}]."
            )
        )
    )

    draft = AnnualSectionExecutor(tmp_path, completion).write_narrative(job_id=job_id, pack=pack)

    assert draft.citation_artifact_keys == (artifact.artifact_key,)
    prompt = completion.calls[0]["user"]
    assert "MSFT launches new AI product" in prompt
    assert "Management guided to strong cloud growth" in prompt
    assert f"[{citation_1}]" in prompt
    assert f"[{citation_2}]" in prompt
    assert "管理层/第三方观点，非 SEC 官方申报" in prompt


def test_narrative_writer_selects_mda_blocks(tmp_path: Path) -> None:
    """Item 7 标题块连同紧随的 MD&A 正文块进入业务概览写作上下文（全文级解析）。"""
    job_id = "job-mda"
    parsed = AnnualParsedDocument(
        source_checksum="source-checksum",
        media_type="text/html",
        parser_name="html",
        blocks=(
            AnnualParsedTextBlock(text="Item 1. Business", locator="offset:1"),
            AnnualParsedTextBlock(text="Company operates in cloud software.", locator="offset:2"),
            AnnualParsedTextBlock(
                text="Item 7. Management's Discussion and Analysis", locator="offset:3"
            ),
            AnnualParsedTextBlock(
                text="Revenue growth was driven by cloud adoption.", locator="offset:4"
            ),
            AnnualParsedTextBlock(
                text="We expect continued investment in AI infrastructure.", locator="offset:5"
            ),
        ),
    )
    ref = ArtifactStore(tmp_path / job_id).write(
        "annual/target/parsed.json", parsed.model_dump_json().encode()
    )
    artifact = _artifact(
        ref.artifact_key, ref.content_checksum, kind=EvidenceKind.TARGET_ANNUAL_FILING
    )
    pack = SectionInputPack(
        section=AnnualSectionKind.BUSINESS_OVERVIEW,
        status=SectionWorkStatus.READY,
        evidence_artifacts=(artifact,),
    )
    citation = build_source_citation_key(
        type("Source", (), {"canonical_url": artifact.source_url})()
    )
    completion = _Completion(
        lambda _: AnnualLlmResult(
            markdown=(
                "## Business overview\nMD&A discussed growth and AI investment. "
                f"[{citation}]"
            )
        )
    )

    draft = AnnualSectionExecutor(tmp_path, completion).write_narrative(job_id=job_id, pack=pack)

    assert draft.citation_artifact_keys == (artifact.artifact_key,)
    prompt = completion.calls[0]["user"]
    # Item 7 标题后的 MD&A 正文块被纳入（全文级解析）。
    assert "Revenue growth was driven by cloud adoption" in prompt
    assert "We expect continued investment in AI infrastructure" in prompt
