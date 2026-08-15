"""P05-10 fault injection: invalid LLM output, guardrail, reflection caps."""

from __future__ import annotations

from invest_research.agents.guardrails import run_with_guardrail, validate_against_schema
from invest_research.domain.models import ReportDraft
from invest_research.domain.quality import QualityAction
from invest_research.flows.reflection import (
    MAX_REVISION_COUNT,
    MAX_SUPPLEMENT_COUNT,
    ReflectionController,
)


def _valid_draft() -> dict[str, object]:
    return {
        "version": "report_draft_v1",
        "title": "MSFT research",
        "markdown": "# MSFT\n> not investment advice",
        "citation_keys": ["claim-1"],
    }


# ---- C. invalid LLM output (schema layer) ----


def test_invalid_llm_json_rejected() -> None:
    """Invalid LLM JSON -> validation fails with readable errors."""
    valid, errors = validate_against_schema("{not json", ReportDraft)
    assert valid is False
    assert errors


def test_llm_output_missing_field_rejected() -> None:
    """Missing required field (title) -> validation fails pointing at it."""
    payload: dict[str, object] = {"version": "report_draft_v1", "markdown": "# x"}
    valid, errors = validate_against_schema(payload, ReportDraft)
    assert valid is False
    assert any("title" in e for e in errors)


def test_llm_output_wrong_type_rejected() -> None:
    """Wrong field type (version=int) -> Pydantic validation fails."""
    payload: dict[str, object] = {
        "version": 123,
        "title": "x",
        "markdown": "# x",
        "citation_keys": ["c"],
    }
    valid, errors = validate_against_schema(payload, ReportDraft)
    assert valid is False
    assert errors


# ---- D. guardrail max attempts ----


def test_guardrail_exhausts_max_attempts() -> None:
    """Guardrail hits max attempts -> invalid, exactly max calls, errors kept."""
    calls = 0

    def producer(hint: str | None) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"version": "bad", "markdown": "# x"}  # always invalid

    outcome = run_with_guardrail(producer, ReportDraft, "hint", max_attempts=3)
    assert outcome.valid is False
    assert outcome.attempts_used == 3
    assert calls == 3  # bounded, not infinite
    assert outcome.errors


def test_guardrail_recovers_before_max() -> None:
    """Guardrail repairs -> returns early, does not waste remaining attempts."""
    calls: list[str | None] = []

    def producer(hint: str | None) -> dict[str, object]:
        calls.append(hint)
        if hint is None:
            return {"version": "report_draft_v1", "markdown": "# x"}
        return _valid_draft()

    outcome = run_with_guardrail(producer, ReportDraft, "hint", max_attempts=3)
    assert outcome.valid is True
    assert outcome.attempts_used == 2
    assert len(calls) == 2


# ---- E. reflection max counts (no infinite reflection) ----


def test_reflection_revision_hits_cap() -> None:
    """Revision hits max -> repeat_reject, no more revisions."""
    ctrl = ReflectionController()
    first = ctrl.step(QualityAction.REVISE_REPORT, revision_used=0, supplement_used=0)
    assert first.outcome == "revise"
    assert first.revision_used == 1

    second = ctrl.step(
        QualityAction.REVISE_REPORT,
        revision_used=first.revision_used,
        supplement_used=0,
    )
    assert second.outcome == "repeat_reject"
    assert second.revision_used == MAX_REVISION_COUNT


def test_reflection_supplement_hits_cap() -> None:
    """Supplement hits max -> repeat_reject, no more supplements."""
    ctrl = ReflectionController()
    first = ctrl.step(QualityAction.SUPPLEMENT_RESEARCH, revision_used=0, supplement_used=0)
    assert first.outcome == "supplement"
    assert first.supplement_used == 1

    second = ctrl.step(
        QualityAction.SUPPLEMENT_RESEARCH,
        revision_used=0,
        supplement_used=first.supplement_used,
    )
    assert second.outcome == "repeat_reject"
    assert second.supplement_used == MAX_SUPPLEMENT_COUNT


def test_reflection_reject_is_terminal() -> None:
    """REJECT action -> direct reject, no loop."""
    ctrl = ReflectionController()
    attempt = ctrl.step(QualityAction.REJECT, revision_used=0, supplement_used=0)
    assert attempt.outcome == "reject"
    assert len(ctrl.history) == 1


def test_reflection_history_is_auditable() -> None:
    """Every reflection decision is recorded in history for audit."""
    ctrl = ReflectionController()
    ctrl.step(QualityAction.REVISE_REPORT, revision_used=0, supplement_used=0)
    ctrl.step(QualityAction.NONE, revision_used=1, supplement_used=0)
    assert [a.outcome for a in ctrl.history] == ["revise", "publish"]
