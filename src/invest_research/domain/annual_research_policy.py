"""P07-06：以证据覆盖与硬边界驱动的纯补证决策。"""

from __future__ import annotations

from collections.abc import Iterable

from invest_research.domain.annual_pipeline import (
    AnnualReadiness,
    CoverageItem,
    CoverageRequirement,
    CoverageStatus,
    ResearchDecision,
    ResearchDecisionAction,
    ResearchDecisionBudget,
    ResearchDecisionReasonCode,
    ResearchNodeKind,
    ResearchObservation,
    SupplementRequest,
)

_CORE_FINANCIAL_REQUIREMENTS = (
    CoverageRequirement.TARGET_ANNUAL_FILING,
    CoverageRequirement.TARGET_FINANCIAL_FACTS,
    CoverageRequirement.COMPARATOR_FINANCIAL_FACTS,
)

_TOOL_FOR_NODE_KIND = {
    ResearchNodeKind.DISCOVER_ANNUAL_FILINGS: "sec_submissions",
    ResearchNodeKind.FETCH_COMPANY_FACTS: "sec_company_facts",
    ResearchNodeKind.REQUEST_SUPPLEMENT: "web_search",
}


class AnnualResearchDecisionPolicy:
    """对一个不可变观察快照生成下一步，不访问网络或修改任何状态。"""

    @classmethod
    def decide(
        cls,
        observation: ResearchObservation,
        *,
        budget: ResearchDecisionBudget | None = None,
    ) -> ResearchDecision:
        active_budget = budget or ResearchDecisionBudget()
        items = {item.requirement: item for item in observation.coverage_ledger.items}

        unresolved_core = cls._unresolved_core(items)
        if unresolved_core:
            return cls._terminal_for_unresolved_core(unresolved_core, observation, active_budget)

        comparator = items.get(CoverageRequirement.COMPARATOR_ANNUAL_FILING)
        if comparator is not None and comparator.status is CoverageStatus.MISSING:
            return cls._request_or_terminal(
                comparator,
                observation,
                active_budget,
                partial_when_stopped=True,
            )

        narrative_gap = cls._first_narrative_gap(items.values())
        if narrative_gap is not None:
            return cls._request_or_terminal(
                narrative_gap,
                observation,
                active_budget,
                partial_when_stopped=True,
            )

        if observation.coverage_ledger.readiness is AnnualReadiness.FULL_READY:
            return ResearchDecision(
                action=ResearchDecisionAction.READY_FOR_ANALYSIS,
                reason="目标 10-K、两年财务事实及上一年度 10-K 均已验证",
                reason_code=ResearchDecisionReasonCode.COVERAGE_COMPLETE,
            )

        if observation.coverage_ledger.readiness is AnnualReadiness.FINANCIAL_ONLY_READY:
            return ResearchDecision(
                action=ResearchDecisionAction.PARTIAL_READY,
                reason="两年财务事实已验证，但上一年度 10-K 叙事证据不可用",
                reason_code=ResearchDecisionReasonCode.CONFIRMED_UNAVAILABLE,
                gap_requirements=(CoverageRequirement.COMPARATOR_ANNUAL_FILING,),
            )

        return ResearchDecision(
            action=ResearchDecisionAction.BLOCKED,
            reason="Coverage Ledger 未形成可安全交付的年度研究状态",
            reason_code=ResearchDecisionReasonCode.COVERAGE_UNRESOLVED,
        )

    @classmethod
    def _terminal_for_unresolved_core(
        cls,
        unresolved: tuple[CoverageItem, ...],
        observation: ResearchObservation,
        budget: ResearchDecisionBudget,
    ) -> ResearchDecision:
        item = unresolved[0]
        if item.status is not CoverageStatus.MISSING:
            return ResearchDecision(
                action=ResearchDecisionAction.BLOCKED,
                reason=f"{item.requirement.value} 尚未形成可审计的缺失结论",
                reason_code=ResearchDecisionReasonCode.COVERAGE_UNRESOLVED,
                gap_requirements=(item.requirement,),
            )
        return cls._request_or_terminal(
            item,
            observation,
            budget,
            partial_when_stopped=False,
        )

    @staticmethod
    def _unresolved_core(
        items: dict[CoverageRequirement, CoverageItem],
    ) -> tuple[CoverageItem, ...]:
        unresolved: list[CoverageItem] = []
        for requirement in _CORE_FINANCIAL_REQUIREMENTS:
            item = items.get(requirement)
            if item is None or item.status is not CoverageStatus.SATISFIED:
                if item is None:
                    # 缺少整个账本条目时没有可审计的补证对象，交由统一阻塞分支处理。
                    return ()
                unresolved.append(item)
        return tuple(unresolved)

    @staticmethod
    def _first_narrative_gap(items: Iterable[CoverageItem]) -> CoverageItem | None:
        narrative_requirements = {
            CoverageRequirement.CURRENT_BUSINESS_OVERVIEW,
            CoverageRequirement.CURRENT_RISK_FACTORS,
            CoverageRequirement.COMPARATOR_RISK_FACTORS,
            CoverageRequirement.COMPARATOR_MANAGEMENT_DISCUSSION,
        }
        return next(
            (
                item
                for item in items
                if item.requirement in narrative_requirements
                and item.status is CoverageStatus.MISSING
            ),
            None,
        )

    @classmethod
    def _request_or_terminal(
        cls,
        item: CoverageItem,
        observation: ResearchObservation,
        budget: ResearchDecisionBudget,
        *,
        partial_when_stopped: bool,
    ) -> ResearchDecision:
        request = SupplementRequest(
            requirement=item.requirement,
            target_fiscal_year=observation.coverage_ledger.target_fiscal_year,
            node_kind=cls._node_kind_for(item.requirement),
        )
        stop_code = cls._stop_code(item.requirement, request.node_kind, observation, budget)
        if stop_code is None:
            return ResearchDecision(
                action=ResearchDecisionAction.CONTINUE_SEARCH,
                reason=f"{item.requirement.value} 缺失：{item.missing_reason}",
                reason_code=ResearchDecisionReasonCode.SUPPLEMENT_REQUIRED,
                gap_requirements=(item.requirement,),
                requested_node_kinds=(request.node_kind,),
                supplement_requests=(request,),
            )

        action = (
            ResearchDecisionAction.PARTIAL_READY
            if partial_when_stopped
            else ResearchDecisionAction.BLOCKED
        )
        return ResearchDecision(
            action=action,
            reason=f"{item.requirement.value} 无法继续补证：{cls._reason_message(stop_code)}",
            reason_code=stop_code,
            gap_requirements=(item.requirement,),
        )

    @staticmethod
    def _node_kind_for(requirement: CoverageRequirement) -> ResearchNodeKind:
        if requirement in {
            CoverageRequirement.TARGET_ANNUAL_FILING,
            CoverageRequirement.COMPARATOR_ANNUAL_FILING,
        }:
            return ResearchNodeKind.DISCOVER_ANNUAL_FILINGS
        if requirement in {
            CoverageRequirement.TARGET_FINANCIAL_FACTS,
            CoverageRequirement.COMPARATOR_FINANCIAL_FACTS,
        }:
            return ResearchNodeKind.FETCH_COMPANY_FACTS
        return ResearchNodeKind.REQUEST_SUPPLEMENT

    @classmethod
    def _stop_code(
        cls,
        requirement: CoverageRequirement,
        node_kind: ResearchNodeKind,
        observation: ResearchObservation,
        budget: ResearchDecisionBudget,
    ) -> ResearchDecisionReasonCode | None:
        if requirement in observation.confirmed_unavailable_requirements:
            return ResearchDecisionReasonCode.CONFIRMED_UNAVAILABLE
        if requirement in observation.attempted_requirements:
            return ResearchDecisionReasonCode.SUPPLEMENT_ALREADY_ATTEMPTED
        if observation.decisions_used >= budget.max_decisions:
            return ResearchDecisionReasonCode.DECISION_BUDGET_EXHAUSTED
        if observation.elapsed_seconds >= budget.max_elapsed_seconds:
            return ResearchDecisionReasonCode.TIME_BUDGET_EXHAUSTED
        if observation.consecutive_no_gain >= budget.max_consecutive_no_gain:
            return ResearchDecisionReasonCode.NO_EVIDENCE_GAIN
        tool_name = _TOOL_FOR_NODE_KIND[node_kind]
        remaining = {
            entry.tool_name: entry.remaining_calls
            for entry in observation.remaining_tool_budget
        }.get(tool_name)
        if remaining is not None and remaining <= 0:
            return ResearchDecisionReasonCode.TOOL_BUDGET_EXHAUSTED
        return None

    @staticmethod
    def _reason_message(reason_code: ResearchDecisionReasonCode) -> str:
        messages = {
            ResearchDecisionReasonCode.CONFIRMED_UNAVAILABLE: "已确认不可得",
            ResearchDecisionReasonCode.SUPPLEMENT_ALREADY_ATTEMPTED: "同类缺口已补证一次",
            ResearchDecisionReasonCode.DECISION_BUDGET_EXHAUSTED: "补证决策次数已耗尽",
            ResearchDecisionReasonCode.TIME_BUDGET_EXHAUSTED: "补证时间已耗尽",
            ResearchDecisionReasonCode.NO_EVIDENCE_GAIN: "连续补证未产生新增有效证据",
            ResearchDecisionReasonCode.TOOL_BUDGET_EXHAUSTED: "对应工具额度已耗尽",
        }
        return messages[reason_code]
