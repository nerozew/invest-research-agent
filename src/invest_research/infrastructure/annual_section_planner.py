"""P07-08：从权限化路由构造独立章节输入包，不生成任何文本。"""

from __future__ import annotations

from invest_research.domain.annual_evidence_routing import (
    AnnualEvidenceRoutingBundle,
    EvidenceRouteStatus,
)
from invest_research.domain.annual_pipeline import EvidenceKind
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    AnnualSectionPlan,
    SectionInputPack,
    SectionWorkStatus,
)


class AnnualSectionPlanner:
    """把 P07-07 的两条受控证据路由拆成四个可独立调度的章节输入。"""

    @classmethod
    def build(cls, routing: AnnualEvidenceRoutingBundle) -> AnnualSectionPlan:
        return AnnualSectionPlan(
            sections=(
                cls._financial(routing),
                cls._business(routing),
                cls._risk(routing),
                cls._events(routing),
            )
        )

    @staticmethod
    def _financial(routing: AnnualEvidenceRoutingBundle) -> SectionInputPack:
        analysis = routing.analysis
        if analysis.status is EvidenceRouteStatus.BLOCKED:
            return SectionInputPack(
                section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
                status=SectionWorkStatus.BLOCKED,
                limitations=analysis.limitations,
            )
        return SectionInputPack(
            section=AnnualSectionKind.FINANCIAL_PERFORMANCE,
            status=AnnualSectionPlanner._status(analysis.status),
            evidence_artifacts=analysis.financial_fact_artifacts,
            comparison_pack=analysis.comparison_pack,
            limitations=analysis.limitations,
        )

    @staticmethod
    def _business(routing: AnnualEvidenceRoutingBundle) -> SectionInputPack:
        pack = AnnualSectionPlanner._narrative_section(
            routing,
            AnnualSectionKind.BUSINESS_OVERVIEW,
            {EvidenceKind.TARGET_ANNUAL_FILING, EvidenceKind.BUSINESS_OVERVIEW},
        )
        # 上年 10-K 缺失只影响叙事同比，不妨碍目标年度业务概览独立写作。
        if pack.status is SectionWorkStatus.PARTIAL and pack.evidence_artifacts:
            return pack.model_copy(update={"status": SectionWorkStatus.READY})
        return pack

    @staticmethod
    def _risk(routing: AnnualEvidenceRoutingBundle) -> SectionInputPack:
        pack = AnnualSectionPlanner._narrative_section(
            routing,
            AnnualSectionKind.RISK_FACTORS,
            {
                EvidenceKind.TARGET_ANNUAL_FILING,
                EvidenceKind.COMPARATOR_ANNUAL_FILING,
                EvidenceKind.RISK_FACTORS,
                EvidenceKind.MANAGEMENT_DISCUSSION,
            },
        )
        if pack.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
            return pack
        has_comparator = any(
            artifact.kind is EvidenceKind.COMPARATOR_ANNUAL_FILING
            for artifact in pack.evidence_artifacts
        )
        if has_comparator:
            return pack
        return pack.model_copy(
            update={
                "status": SectionWorkStatus.PARTIAL,
                "limitations": AnnualSectionPlanner._unique(
                    (*pack.limitations, "上一年度 10-K 不可用，禁止风险与 MD&A 叙事同比")
                ),
            }
        )

    @staticmethod
    def _events(routing: AnnualEvidenceRoutingBundle) -> SectionInputPack:
        if routing.narrative.status is EvidenceRouteStatus.BLOCKED:
            return SectionInputPack(
                section=AnnualSectionKind.MATERIAL_EVENTS,
                status=SectionWorkStatus.BLOCKED,
                limitations=routing.narrative.limitations,
            )
        events = tuple(
            artifact
            for artifact in routing.narrative.narrative_artifacts
            if artifact.kind is EvidenceKind.MATERIAL_EVENT
        )
        if not events:
            return SectionInputPack(
                section=AnnualSectionKind.MATERIAL_EVENTS,
                status=SectionWorkStatus.NOT_APPLICABLE,
                limitations=("截至数据截止日没有已验证的重大事件证据",),
            )
        return SectionInputPack(
            section=AnnualSectionKind.MATERIAL_EVENTS,
            status=AnnualSectionPlanner._status(routing.narrative.status),
            evidence_artifacts=events,
            limitations=routing.narrative.limitations,
        )

    @staticmethod
    def _narrative_section(
        routing: AnnualEvidenceRoutingBundle,
        section: AnnualSectionKind,
        allowed_kinds: set[EvidenceKind],
    ) -> SectionInputPack:
        narrative = routing.narrative
        if narrative.status is EvidenceRouteStatus.BLOCKED:
            return SectionInputPack(
                section=section,
                status=SectionWorkStatus.BLOCKED,
                limitations=narrative.limitations,
            )
        artifacts = tuple(
            artifact for artifact in narrative.narrative_artifacts if artifact.kind in allowed_kinds
        )
        if not artifacts:
            return SectionInputPack(
                section=section,
                status=SectionWorkStatus.BLOCKED,
                limitations=AnnualSectionPlanner._unique(
                    (*narrative.limitations, f"没有可路由的 {section.value} 证据")
                ),
            )
        return SectionInputPack(
            section=section,
            status=AnnualSectionPlanner._status(narrative.status),
            evidence_artifacts=artifacts,
            limitations=narrative.limitations,
        )

    @staticmethod
    def _status(status: EvidenceRouteStatus) -> SectionWorkStatus:
        return (
            SectionWorkStatus.READY
            if status is EvidenceRouteStatus.READY
            else SectionWorkStatus.PARTIAL
        )

    @staticmethod
    def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value))
