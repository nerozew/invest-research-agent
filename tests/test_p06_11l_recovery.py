"""P06-11L：可复现运行基线与 Finalizer/步骤诊断修复（全离线）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnalysisSelectionDraft,
    CompanyIdentity,
    FinancialAnalysisPack,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    ResearchSelectionDraft,
    Source,
    SourceType,
)
from invest_research.infrastructure.finalizers.deepseek_json_object_finalizer import (
    DeepSeekJsonObjectFinalizer,
    _build_schema_example,
)
from invest_research.infrastructure.flow_wiring import LiveResearchFlowRunner


def _config() -> LLMConfig:
    return LLMConfig(
        provider="openai_compatible",
        vendor="deepseek",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("sk-test-placeholder"),
        model_research="deepseek-research",
        model_analysis="deepseek-analysis",
        model_writer="deepseek-writer",
        temperature=0.2,
        timeout=30.0,
        enable_thinking=False,
    )


def _response(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ]
    )


def _valid_research_json() -> str:
    return json.dumps(
        {
            "version": "research_selection_draft_v1",
            "schema_version": "research_selection_draft_v1",
            "as_of_date": "2025-10-31",
            "selected_source_urls": ["https://www.sec.gov/example"],
            "coverage_notes": None,
            "conflicts": [],
        }
    )


def test_schema_examples_are_values_and_validate() -> None:
    research = json.loads(_build_schema_example(ResearchSelectionDraft))
    analysis = json.loads(_build_schema_example(AnalysisSelectionDraft))

    assert "properties" not in research
    assert research["version"] == "research_selection_draft_v1"
    assert analysis["limitations"]
    ResearchSelectionDraft.model_validate(research)
    AnalysisSelectionDraft.model_validate(analysis)


def test_blank_version_is_deterministically_filled_without_llm() -> None:
    client = MagicMock()
    finalizer = DeepSeekJsonObjectFinalizer(_config(), client=client)

    result = finalizer.finalize(
        {
            "version": " ",
            "as_of_date": "2025-10-31",
            "selected_source_urls": [],
            "coverage_notes": None,
            "conflicts": [],
        },
        ResearchSelectionDraft,
        role="research",
    )

    assert result.version == "research_selection_draft_v1"
    client.chat.completions.create.assert_not_called()


def test_repair_receives_first_invalid_json_and_field_errors() -> None:
    invalid = json.dumps(
        {
            "version": "research_selection_draft_v1",
            "as_of_date": "not-a-date",
            "selected_source_urls": [],
        }
    )
    create = MagicMock(side_effect=[_response(invalid), _response(_valid_research_json())])
    client = MagicMock()
    client.chat.completions.create = create
    events: list[dict[str, object]] = []
    finalizer = DeepSeekJsonObjectFinalizer(
        _config(), client=client, diagnostic_callback=events.append
    )

    result = finalizer.finalize(
        "Agent 的原始自然语言，不应作为第二次修复主体",
        ResearchSelectionDraft,
        role="research",
    )

    assert result.version == "research_selection_draft_v1"
    repair_text = create.call_args_list[1].kwargs["messages"][1]["content"]
    assert invalid in repair_text
    assert "as_of_date" in repair_text
    assert "Agent 的原始自然语言" not in repair_text
    assert [event["event"] for event in events] == [
        "response",
        "validation_failed",
        "response",
    ]
    assert "sk-test-placeholder" not in json.dumps(events)


def test_staged_flow_transitions_documents_through_running() -> None:
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 10, 31))
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=request.as_of_date,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://www.sec.gov/example",
                accessed_at=request.as_of_date,
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v2",
        schema_version="analysis_pack_v2",
        period_end=request.as_of_date,
        limitations=["离线步骤状态测试没有财务事实"],
        completeness=AnalysisCompleteness.PARTIAL,
    )
    report = ReportDraft(version="report_draft_v1", title="MSFT", markdown="# MSFT")
    runner = LiveResearchFlowRunner(_config())
    marks: list[tuple[str, str]] = []
    runner._mark = lambda step, action: marks.append((step, action))  # type: ignore[method-assign]
    runner._exec_research_stage = lambda *_: research  # type: ignore[method-assign]
    runner._exec_analysis_stage = lambda *_: analysis  # type: ignore[method-assign]
    runner._exec_writer_stage = lambda *_: report  # type: ignore[method-assign]

    runner._run_staged(request, SimpleNamespace())

    assert marks[:6] == [
        ("02_research", "running"),
        ("02_research", "succeeded"),
        ("03_documents", "running"),
        ("03_documents", "succeeded"),
        ("04_analysis", "running"),
        ("04_analysis", "succeeded"),
    ]


def test_compose_passes_bounded_failure_diagnostics() -> None:
    compose = (Path(__file__).parents[1] / "compose.yml").read_text(encoding="utf-8")
    assert "DIAGNOSTIC_CAPTURE_MODE: ${DIAGNOSTIC_CAPTURE_MODE:-failure_payload}" in compose
    assert "DIAGNOSTIC_MAX_EVENT_BYTES" in compose
    assert "DIAGNOSTIC_MAX_BUNDLE_BYTES" in compose
    assert "DIAGNOSTIC_MAX_EVENTS" in compose
    assert "DIAGNOSTIC_RETENTION_DAYS" in compose
