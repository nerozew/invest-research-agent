"""P07-06：受控补证策略只基于账本快照作决定。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from invest_research.domain.annual_pipeline import (
    AnnualReadiness,
    CoverageItem,
    CoverageLedger,
    CoverageRequirement,
    CoverageStatus,
    ResearchDecision,
    ResearchDecisionAction,
    ResearchDecisionReasonCode,
    ResearchNodeKind,
    ResearchObservation,
    SupplementRequest,
    ToolBudgetAvailability,
)
from invest_research.domain.annual_research_policy import AnnualResearchDecisionPolicy
from invest_research.infrastructure.tool_budget import ToolBudget


def _satisfied(requirement: CoverageRequirement) -> CoverageItem:
    return CoverageItem(
        requirement=requirement,
        status=CoverageStatus.SATISFIED,
        evidence_artifact_keys=(f"annual/{requirement.value}",),
    )


def _missing(requirement: CoverageRequirement, reason: str = "fixture 缺失") -> CoverageItem:
    return CoverageItem(
        requirement=requirement,
        status=CoverageStatus.MISSING,
        missing_reason=reason,
    )


def _ledger(
    *,
    target_filing: CoverageItem | None = None,
    target_facts: CoverageItem | None = None,
    comparator_facts: CoverageItem | None = None,
    comparator_filing: CoverageItem | None = None,
    readiness: AnnualReadiness = AnnualReadiness.FULL_READY,
    extras: tuple[CoverageItem, ...] = (),
) -> CoverageLedger:
    return CoverageLedger(
        target_fiscal_year=2025,
        readiness=readiness,
        items=(
            target_filing or _satisfied(CoverageRequirement.TARGET_ANNUAL_FILING),
            target_facts or _satisfied(CoverageRequirement.TARGET_FINANCIAL_FACTS),
            comparator_facts or _satisfied(CoverageRequirement.COMPARATOR_FINANCIAL_FACTS),
            comparator_filing or _satisfied(CoverageRequirement.COMPARATOR_ANNUAL_FILING),
            *extras,
        ),
    )


def _observation(ledger: CoverageLedger, **changes: object) -> ResearchObservation:
    return ResearchObservation(coverage_ledger=ledger, **changes)


def test_full_ready_moves_directly_to_analysis() -> None:
    decision = AnnualResearchDecisionPolicy.decide(_observation(_ledger()))

    assert decision.action is ResearchDecisionAction.READY_FOR_ANALYSIS
    assert decision.reason_code is ResearchDecisionReasonCode.COVERAGE_COMPLETE


def test_comparator_10k_is_requested_once_then_financial_report_is_partial_ready() -> None:
    ledger = _ledger(
        comparator_filing=_missing(CoverageRequirement.COMPARATOR_ANNUAL_FILING),
        readiness=AnnualReadiness.FINANCIAL_ONLY_READY,
    )
    first = AnnualResearchDecisionPolicy.decide(_observation(ledger))
    second = AnnualResearchDecisionPolicy.decide(
        _observation(
            ledger,
            attempted_requirements=(CoverageRequirement.COMPARATOR_ANNUAL_FILING,),
        )
    )

    assert first.action is ResearchDecisionAction.CONTINUE_SEARCH
    assert first.supplement_requests[0].node_kind is ResearchNodeKind.DISCOVER_ANNUAL_FILINGS
    assert second.action is ResearchDecisionAction.PARTIAL_READY
    assert second.reason_code is ResearchDecisionReasonCode.SUPPLEMENT_ALREADY_ATTEMPTED


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    (
        ({"elapsed_seconds": 120.0}, ResearchDecisionReasonCode.TIME_BUDGET_EXHAUSTED),
        ({"decisions_used": 2}, ResearchDecisionReasonCode.DECISION_BUDGET_EXHAUSTED),
        ({"consecutive_no_gain": 1}, ResearchDecisionReasonCode.NO_EVIDENCE_GAIN),
        (
            {
                "remaining_tool_budget": (
                    ToolBudgetAvailability(tool_name="sec_submissions", remaining_calls=0),
                )
            },
            ResearchDecisionReasonCode.TOOL_BUDGET_EXHAUSTED,
        ),
    ),
)
def test_comparator_gap_stops_at_hard_bounds(
    changes: dict[str, object], reason_code: ResearchDecisionReasonCode
) -> None:
    ledger = _ledger(
        comparator_filing=_missing(CoverageRequirement.COMPARATOR_ANNUAL_FILING),
        readiness=AnnualReadiness.FINANCIAL_ONLY_READY,
    )

    decision = AnnualResearchDecisionPolicy.decide(_observation(ledger, **changes))

    assert decision.action is ResearchDecisionAction.PARTIAL_READY
    assert decision.reason_code is reason_code


def test_missing_target_10k_requests_discovery_then_blocks_when_confirmed_unavailable() -> None:
    ledger = _ledger(
        target_filing=_missing(CoverageRequirement.TARGET_ANNUAL_FILING),
        readiness=AnnualReadiness.NARRATIVE_ONLY_READY,
    )
    first = AnnualResearchDecisionPolicy.decide(_observation(ledger))
    second = AnnualResearchDecisionPolicy.decide(
        _observation(
            ledger,
            confirmed_unavailable_requirements=(CoverageRequirement.TARGET_ANNUAL_FILING,),
        )
    )

    assert first.action is ResearchDecisionAction.CONTINUE_SEARCH
    assert first.supplement_requests[0].node_kind is ResearchNodeKind.DISCOVER_ANNUAL_FILINGS
    assert second.action is ResearchDecisionAction.BLOCKED
    assert second.reason_code is ResearchDecisionReasonCode.CONFIRMED_UNAVAILABLE


def test_missing_financial_facts_requests_facts_then_blocks_after_no_gain() -> None:
    ledger = _ledger(
        target_facts=_missing(CoverageRequirement.TARGET_FINANCIAL_FACTS),
        readiness=AnnualReadiness.BLOCKED,
    )
    first = AnnualResearchDecisionPolicy.decide(_observation(ledger))
    second = AnnualResearchDecisionPolicy.decide(
        _observation(ledger, consecutive_no_gain=1)
    )

    assert first.supplement_requests[0].node_kind is ResearchNodeKind.FETCH_COMPANY_FACTS
    assert second.action is ResearchDecisionAction.BLOCKED
    assert second.reason_code is ResearchDecisionReasonCode.NO_EVIDENCE_GAIN


def test_pending_core_coverage_blocks_without_starting_a_search() -> None:
    pending_target_facts = CoverageItem(
        requirement=CoverageRequirement.TARGET_FINANCIAL_FACTS,
        status=CoverageStatus.PENDING,
    )
    ledger = _ledger(target_facts=pending_target_facts, readiness=AnnualReadiness.BLOCKED)

    decision = AnnualResearchDecisionPolicy.decide(_observation(ledger))

    assert decision.action is ResearchDecisionAction.BLOCKED
    assert decision.reason_code is ResearchDecisionReasonCode.COVERAGE_UNRESOLVED


def test_missing_narrative_evidence_uses_controlled_supplement() -> None:
    ledger = _ledger(
        extras=(_missing(CoverageRequirement.CURRENT_RISK_FACTORS),),
    )

    decision = AnnualResearchDecisionPolicy.decide(_observation(ledger))

    assert decision.action is ResearchDecisionAction.CONTINUE_SEARCH
    assert decision.supplement_requests[0].node_kind is ResearchNodeKind.REQUEST_SUPPLEMENT


def test_contract_rejects_unmapped_or_repeatable_continue_search_requests() -> None:
    with pytest.raises(ValidationError):
        SupplementRequest(
            requirement=CoverageRequirement.TARGET_FINANCIAL_FACTS,
            target_fiscal_year=2025,
            node_kind=ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,
        )
    request = SupplementRequest(
        requirement=CoverageRequirement.TARGET_FINANCIAL_FACTS,
        target_fiscal_year=2025,
        node_kind=ResearchNodeKind.FETCH_COMPANY_FACTS,
    )
    with pytest.raises(ValidationError):
        ResearchDecision(
            action=ResearchDecisionAction.CONTINUE_SEARCH,
            reason="重复请求",
            gap_requirements=(
                CoverageRequirement.TARGET_FINANCIAL_FACTS,
                CoverageRequirement.TARGET_FINANCIAL_FACTS,
            ),
            requested_node_kinds=(ResearchNodeKind.FETCH_COMPANY_FACTS,),
            supplement_requests=(request,),
        )


def test_policy_does_not_consume_tool_budget() -> None:
    budget = ToolBudget({"sec_submissions": 1})
    ledger = _ledger(
        comparator_filing=_missing(CoverageRequirement.COMPARATOR_ANNUAL_FILING),
        readiness=AnnualReadiness.FINANCIAL_ONLY_READY,
    )

    AnnualResearchDecisionPolicy.decide(
        _observation(
            ledger,
            remaining_tool_budget=(
                ToolBudgetAvailability(tool_name="sec_submissions", remaining_calls=1),
            ),
        )
    )

    assert budget.snapshot() == {}
