"""P07-07：对 P07 工件执行无副作用的年度证据最小权限路由。"""

from __future__ import annotations

from invest_research.domain.annual_evidence_routing import (
    AnnualAnalysisEvidenceBundle,
    AnnualEvidenceRoutingBundle,
    AnnualNarrativeEvidenceBundle,
    EvidenceRouteStatus,
)
from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    EvidenceKind,
    EvidenceValidationStatus,
    ResearchDecision,
    ResearchDecisionAction,
)
from invest_research.domain.models import AnnualComparisonPack
from invest_research.infrastructure.annual_evidence_fanout import AnnualEvidenceBundle

_NARRATIVE_KINDS = frozenset(
    {
        EvidenceKind.TARGET_ANNUAL_FILING,
        EvidenceKind.COMPARATOR_ANNUAL_FILING,
        EvidenceKind.BUSINESS_OVERVIEW,
        EvidenceKind.RISK_FACTORS,
        EvidenceKind.MANAGEMENT_DISCUSSION,
        EvidenceKind.MATERIAL_EVENT,
        EvidenceKind.ANALYST_OPINION,
        EvidenceKind.RATING_AGENCY,
    }
)


class AnnualEvidenceRouter:
    """只路由已验证工件；不读取正文、不下载、不写入也不调用 Agent。"""

    @classmethod
    def route(
        cls,
        *,
        evidence: AnnualEvidenceBundle,
        comparison_pack: AnnualComparisonPack,
        decision: ResearchDecision,
    ) -> AnnualEvidenceRoutingBundle:
        if decision.action in {
            ResearchDecisionAction.CONTINUE_SEARCH,
            ResearchDecisionAction.BLOCKED,
        }:
            return cls._fully_blocked(decision.action, decision.reason, comparison_pack.limitations)

        analysis = cls._analysis_route(evidence, comparison_pack)
        narrative = cls._narrative_route(evidence, decision)
        limitations = cls._unique(
            (*analysis.limitations, *narrative.limitations, *comparison_pack.limitations)
        )
        return AnnualEvidenceRoutingBundle(
            decision_action=decision.action,
            analysis=analysis,
            narrative=narrative,
            limitations=limitations,
        )

    @staticmethod
    def _fully_blocked(
        action: ResearchDecisionAction,
        reason: str,
        comparison_limitations: tuple[str, ...],
    ) -> AnnualEvidenceRoutingBundle:
        limitations = AnnualEvidenceRouter._unique((reason, *comparison_limitations))
        return AnnualEvidenceRoutingBundle(
            decision_action=action,
            analysis=AnnualAnalysisEvidenceBundle(
                status=EvidenceRouteStatus.BLOCKED,
                limitations=limitations,
            ),
            narrative=AnnualNarrativeEvidenceBundle(
                status=EvidenceRouteStatus.BLOCKED,
                limitations=limitations,
            ),
            limitations=limitations,
        )

    @classmethod
    def _analysis_route(
        cls,
        evidence: AnnualEvidenceBundle,
        comparison_pack: AnnualComparisonPack,
    ) -> AnnualAnalysisEvidenceBundle:
        limitations = list(comparison_pack.limitations)
        financial_artifacts = tuple(
            artifact
            for artifact in evidence.evidence_artifacts
            if artifact.validation_status is EvidenceValidationStatus.VALIDATED
            and artifact.kind is EvidenceKind.FINANCIAL_FACT_SET
        )
        rejected = sum(
            1
            for artifact in evidence.evidence_artifacts
            if artifact.kind in {EvidenceKind.COMPANY_FACTS, EvidenceKind.FINANCIAL_FACT_SET}
            and artifact.validation_status is not EvidenceValidationStatus.VALIDATED
        )
        if rejected:
            limitations.append(f"{rejected} 个未验证财务工件未被路由")
        consistency_errors = cls._comparison_consistency_errors(evidence, comparison_pack)
        if comparison_pack.status is AnnualComparisonStatus.BLOCKED:
            consistency_errors.append("AnnualComparisonPack 已阻塞")
        if not financial_artifacts:
            consistency_errors.append("没有可路由的已验证 Financial Fact Set")
        if consistency_errors:
            return AnnualAnalysisEvidenceBundle(
                status=EvidenceRouteStatus.BLOCKED,
                limitations=cls._unique((*limitations, *consistency_errors)),
            )
        status = (
            EvidenceRouteStatus.PARTIAL
            if comparison_pack.status is AnnualComparisonStatus.PARTIAL
            else EvidenceRouteStatus.READY
        )
        return AnnualAnalysisEvidenceBundle(
            status=status,
            comparison_pack=comparison_pack,
            financial_fact_artifacts=financial_artifacts,
            limitations=cls._unique(limitations),
        )

    @classmethod
    def _narrative_route(
        cls,
        evidence: AnnualEvidenceBundle,
        decision: ResearchDecision,
    ) -> AnnualNarrativeEvidenceBundle:
        allowed_kinds = _NARRATIVE_KINDS
        if decision.action is ResearchDecisionAction.PARTIAL_READY:
            # comparator 缺失时不得让 Writer 假设它可以进行叙事同比。
            allowed_kinds = allowed_kinds - {EvidenceKind.COMPARATOR_ANNUAL_FILING}
        artifacts = tuple(
            artifact
            for artifact in evidence.evidence_artifacts
            if artifact.validation_status is EvidenceValidationStatus.VALIDATED
            and artifact.kind in allowed_kinds
        )
        limitations = cls._ledger_limitations(evidence)
        rejected = sum(
            1
            for artifact in evidence.evidence_artifacts
            if artifact.kind in _NARRATIVE_KINDS
            and artifact.validation_status is not EvidenceValidationStatus.VALIDATED
        )
        if rejected:
            limitations.append(f"{rejected} 个未验证叙事工件未被路由")
        if decision.action is ResearchDecisionAction.PARTIAL_READY:
            limitations.append(decision.reason)
        if not artifacts:
            return AnnualNarrativeEvidenceBundle(
                status=EvidenceRouteStatus.BLOCKED,
                limitations=cls._unique((*limitations, "没有可路由的已验证叙事证据")),
            )
        status = (
            EvidenceRouteStatus.PARTIAL
            if decision.action is ResearchDecisionAction.PARTIAL_READY or limitations
            else EvidenceRouteStatus.READY
        )
        return AnnualNarrativeEvidenceBundle(
            status=status,
            narrative_artifacts=artifacts,
            limitations=cls._unique(limitations),
        )

    @staticmethod
    def _comparison_consistency_errors(
        evidence: AnnualEvidenceBundle,
        comparison_pack: AnnualComparisonPack,
    ) -> list[str]:
        errors: list[str] = []
        target_year = evidence.coverage_ledger.target_fiscal_year
        if target_year is None or comparison_pack.target_fiscal_year != target_year:
            errors.append("AnnualComparisonPack 目标财年与 Coverage Ledger 不一致")
        expected_comparator_year = target_year - 1 if target_year is not None else None
        if comparison_pack.comparator_fiscal_year != expected_comparator_year:
            errors.append("AnnualComparisonPack 上一财年与 Coverage Ledger 不一致")
        facts_artifacts = [
            artifact
            for artifact in evidence.evidence_artifacts
            if artifact.kind is EvidenceKind.COMPANY_FACTS
            and artifact.validation_status is EvidenceValidationStatus.VALIDATED
        ]
        if len(facts_artifacts) != 1:
            errors.append("缺少唯一的已验证 Company Facts 工件用于校验比较包")
            return errors
        facts_artifact = facts_artifacts[0]
        fingerprint = comparison_pack.input_fingerprint
        if (
            fingerprint.company_facts_artifact_key != facts_artifact.artifact_key
            or fingerprint.company_facts_checksum != facts_artifact.content_checksum
        ):
            errors.append("AnnualComparisonPack 的 Company Facts 工件键或 checksum 不匹配")
        return errors

    @staticmethod
    def _ledger_limitations(evidence: AnnualEvidenceBundle) -> list[str]:
        return [
            item.missing_reason
            for item in evidence.coverage_ledger.items
            if item.missing_reason is not None
        ]

    @staticmethod
    def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value))
