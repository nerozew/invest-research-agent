"""P07-07：年度证据最小权限路由的不可变领域契约。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
    ResearchDecisionAction,
)
from invest_research.domain.models import AnnualComparisonPack

_ANALYSIS_KINDS = frozenset({EvidenceKind.FINANCIAL_FACT_SET})
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


class EvidenceRouteStatus(StrEnum):
    """单个下游消费者可安全消费证据的状态。"""

    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class AnnualAnalysisEvidenceBundle(BaseModel):
    """Analysis 的最小上下文：比较包与已验证财务事实引用。"""

    model_config = ConfigDict(frozen=True)

    status: EvidenceRouteStatus
    comparison_pack: AnnualComparisonPack | None = None
    financial_fact_artifacts: tuple[EvidenceArtifact, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_route(self) -> "AnnualAnalysisEvidenceBundle":
        if any(
            artifact.validation_status is not EvidenceValidationStatus.VALIDATED
            or artifact.kind not in _ANALYSIS_KINDS
            for artifact in self.financial_fact_artifacts
        ):
            raise ValueError("Analysis 路由只能包含已验证的 Financial Fact Set")
        if self.status is EvidenceRouteStatus.BLOCKED:
            if (
                self.comparison_pack is not None
                or self.financial_fact_artifacts
                or not self.limitations
            ):
                raise ValueError("blocked Analysis 路由不得暴露证据，且必须说明限制")
        elif self.comparison_pack is None:
            raise ValueError("可用 Analysis 路由必须包含 AnnualComparisonPack")
        elif self.comparison_pack.status is AnnualComparisonStatus.BLOCKED:
            raise ValueError("blocked AnnualComparisonPack 不得进入 Analysis 路由")
        return self


class AnnualNarrativeEvidenceBundle(BaseModel):
    """未来 Section Writer 的最小叙事证据上下文。"""

    model_config = ConfigDict(frozen=True)

    status: EvidenceRouteStatus
    narrative_artifacts: tuple[EvidenceArtifact, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_route(self) -> "AnnualNarrativeEvidenceBundle":
        if any(
            artifact.validation_status is not EvidenceValidationStatus.VALIDATED
            or artifact.kind not in _NARRATIVE_KINDS
            for artifact in self.narrative_artifacts
        ):
            raise ValueError("Writer 路由只能包含已验证的叙事类证据")
        if self.status is EvidenceRouteStatus.BLOCKED:
            if self.narrative_artifacts or not self.limitations:
                raise ValueError("blocked Writer 路由不得暴露证据，且必须说明限制")
        elif not self.narrative_artifacts:
            raise ValueError("可用 Writer 路由必须包含至少一个已验证叙事证据")
        return self


class AnnualEvidenceRoutingBundle(BaseModel):
    """同一年度任务交给 Analysis 与 Section Writer 的权限化证据集合。"""

    model_config = ConfigDict(frozen=True)

    decision_action: ResearchDecisionAction
    analysis: AnnualAnalysisEvidenceBundle
    narrative: AnnualNarrativeEvidenceBundle
    limitations: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_decision_boundary(self) -> "AnnualEvidenceRoutingBundle":
        if self.decision_action in {
            ResearchDecisionAction.CONTINUE_SEARCH,
            ResearchDecisionAction.BLOCKED,
        } and (
            self.analysis.status is not EvidenceRouteStatus.BLOCKED
            or self.narrative.status is not EvidenceRouteStatus.BLOCKED
        ):
            raise ValueError("补证未结束或研究已阻塞时不得向下游路由证据")
        return self
