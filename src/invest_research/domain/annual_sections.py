"""P07-08：可独立调度的年度章节输入、分析与草稿契约。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
)
from invest_research.domain.models import AnnualComparisonPack


class AnnualSectionKind(StrEnum):
    FINANCIAL_PERFORMANCE = "financial_performance"
    BUSINESS_OVERVIEW = "business_overview"
    RISK_FACTORS = "risk_factors"
    MATERIAL_EVENTS = "material_events"


class SectionWorkStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


_NARRATIVE_KINDS_BY_SECTION = {
    AnnualSectionKind.BUSINESS_OVERVIEW: frozenset(
        {EvidenceKind.TARGET_ANNUAL_FILING, EvidenceKind.BUSINESS_OVERVIEW}
    ),
    AnnualSectionKind.RISK_FACTORS: frozenset(
        {
            EvidenceKind.TARGET_ANNUAL_FILING,
            EvidenceKind.COMPARATOR_ANNUAL_FILING,
            EvidenceKind.RISK_FACTORS,
            EvidenceKind.MANAGEMENT_DISCUSSION,
        }
    ),
    AnnualSectionKind.MATERIAL_EVENTS: frozenset({EvidenceKind.MATERIAL_EVENT}),
}


class SectionInputPack(BaseModel):
    """单个章节被允许消费的最小、已验证输入。"""

    model_config = ConfigDict(frozen=True)

    section: AnnualSectionKind
    status: SectionWorkStatus
    evidence_artifacts: tuple[EvidenceArtifact, ...] = ()
    comparison_pack: AnnualComparisonPack | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_inputs(self) -> "SectionInputPack":
        if any(
            artifact.validation_status is not EvidenceValidationStatus.VALIDATED
            for artifact in self.evidence_artifacts
        ):
            raise ValueError("章节输入只能包含已验证 EvidenceArtifact")
        if self.section is AnnualSectionKind.FINANCIAL_PERFORMANCE:
            self._validate_financial_inputs()
        else:
            self._validate_narrative_inputs()
        return self

    def _validate_financial_inputs(self) -> None:
        if any(
            artifact.kind is not EvidenceKind.FINANCIAL_FACT_SET
            for artifact in self.evidence_artifacts
        ):
            raise ValueError("财务章节只能接收 Financial Fact Set")
        if self.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
            if self.evidence_artifacts or self.comparison_pack is not None or not self.limitations:
                raise ValueError("不可用财务章节不得暴露证据，且必须说明限制")
            return
        if not self.evidence_artifacts or self.comparison_pack is None:
            raise ValueError("可用财务章节必须包含事实集和 AnnualComparisonPack")
        if self.comparison_pack.status is AnnualComparisonStatus.BLOCKED:
            raise ValueError("blocked AnnualComparisonPack 不得进入财务章节")

    def _validate_narrative_inputs(self) -> None:
        if self.comparison_pack is not None:
            raise ValueError("非财务章节不得接收 AnnualComparisonPack")
        allowed = _NARRATIVE_KINDS_BY_SECTION[self.section]
        if any(artifact.kind not in allowed for artifact in self.evidence_artifacts):
            raise ValueError(f"{self.section.value} 包含职责不匹配的叙事证据")
        if self.status is SectionWorkStatus.BLOCKED:
            if self.evidence_artifacts or not self.limitations:
                raise ValueError("blocked 章节不得暴露证据，且必须说明限制")
        elif self.status is SectionWorkStatus.NOT_APPLICABLE:
            if self.evidence_artifacts:
                raise ValueError("not_applicable 章节不得携带证据")
        elif not self.evidence_artifacts:
            raise ValueError("可用叙事章节必须包含至少一个证据工件")

    @property
    def allowed_artifact_keys(self) -> frozenset[str]:
        return frozenset(artifact.artifact_key for artifact in self.evidence_artifacts)


class SectionAnalysisPack(BaseModel):
    """未来 Analysis 节点针对财务章节的结构化输出边界。"""

    model_config = ConfigDict(frozen=True)

    section_input: SectionInputPack
    status: SectionWorkStatus
    analysis_notes: str | None = None
    citation_artifact_keys: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_analysis(self) -> "SectionAnalysisPack":
        if self.section_input.section is not AnnualSectionKind.FINANCIAL_PERFORMANCE:
            raise ValueError("SectionAnalysisPack 只用于财务章节")
        if not set(self.citation_artifact_keys).issubset(self.section_input.allowed_artifact_keys):
            raise ValueError("财务分析引用了未授予的证据工件")
        if self.status in {SectionWorkStatus.READY, SectionWorkStatus.PARTIAL} and (
            self.section_input.status
            in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}
        ):
            raise ValueError("不可用财务输入不得生成可交付的财务分析")
        if self.status is SectionWorkStatus.BLOCKED and (
            self.analysis_notes is not None or self.citation_artifact_keys or not self.limitations
        ):
            raise ValueError("blocked 财务分析不得输出内容或引用，且必须说明限制")
        return self


class SectionDraft(BaseModel):
    """未来章节 Writer 的受控输出；财务草稿只能依赖 Analysis Pack。"""

    model_config = ConfigDict(frozen=True)

    section: AnnualSectionKind
    status: SectionWorkStatus
    markdown: str | None = None
    citation_artifact_keys: tuple[str, ...] = ()
    section_input: SectionInputPack | None = None
    financial_analysis: SectionAnalysisPack | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_draft(self) -> "SectionDraft":
        if self.section is AnnualSectionKind.FINANCIAL_PERFORMANCE:
            if self.financial_analysis is None or self.section_input is not None:
                raise ValueError("财务章节草稿必须且只能引用 SectionAnalysisPack")
            allowed_keys = self.financial_analysis.section_input.allowed_artifact_keys
            upstream_status = self.financial_analysis.status
        else:
            if self.section_input is None or self.financial_analysis is not None:
                raise ValueError("非财务章节草稿必须且只能引用对应 SectionInputPack")
            if self.section_input.section is not self.section:
                raise ValueError("章节草稿与 SectionInputPack 类型不一致")
            allowed_keys = self.section_input.allowed_artifact_keys
            upstream_status = self.section_input.status
        if not set(self.citation_artifact_keys).issubset(allowed_keys):
            raise ValueError("章节草稿引用了未授予的证据工件")
        if self.status in {SectionWorkStatus.READY, SectionWorkStatus.PARTIAL} and (
            upstream_status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}
        ):
            raise ValueError("不可用上游输入不得生成可交付章节草稿")
        if self.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
            if self.markdown is not None or self.citation_artifact_keys or not self.limitations:
                raise ValueError("不可交付章节不得生成内容或引用，且必须说明限制")
        elif not self.markdown:
            raise ValueError("可交付章节草稿必须包含 markdown")
        return self


class AnnualSectionPlan(BaseModel):
    """四个独立章节的可调度输入计划，不决定执行顺序。"""

    model_config = ConfigDict(frozen=True)

    sections: tuple[SectionInputPack, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _validate_sections(self) -> "AnnualSectionPlan":
        kinds = {section.section for section in self.sections}
        if kinds != set(AnnualSectionKind):
            raise ValueError("年度章节计划必须且只能包含四类章节各一项")
        return self
