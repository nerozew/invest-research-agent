"""P07-09：年度章节汇总、发布门禁与有界修订的领域契约。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    SectionDraft,
    SectionWorkStatus,
)


class AnnualFinalizationStatus(StrEnum):
    READY_FOR_FINAL_WRITER = "ready_for_final_writer"
    PARTIAL_READY_FOR_FINAL_WRITER = "partial_ready_for_final_writer"
    NEEDS_SECTION_REVISION = "needs_section_revision"
    BLOCKED = "blocked"


class AnnualFinalizationIssue(BaseModel):
    """最终门禁发现的结构化、可审计问题。"""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    section: AnnualSectionKind | None = None
    revisable: bool = False


class SectionRevisionRequest(BaseModel):
    """一次且仅一次的章节定向修订请求；不得扩大研究证据范围。"""

    model_config = ConfigDict(frozen=True)

    revision_number: int = Field(ge=1, le=1)
    target_sections: tuple[AnnualSectionKind, ...] = Field(min_length=1)
    issues: tuple[AnnualFinalizationIssue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_request(self) -> "SectionRevisionRequest":
        if len(set(self.target_sections)) != len(self.target_sections):
            raise ValueError("章节修订请求不能包含重复章节")
        issue_sections = {issue.section for issue in self.issues if issue.section is not None}
        if not issue_sections.issubset(set(self.target_sections)):
            raise ValueError("章节修订问题必须指向 target_sections 中的章节")
        if any(not issue.revisable for issue in self.issues):
            raise ValueError("章节修订请求不能包含不可修复问题")
        return self


class AnnualFinalizedSection(BaseModel):
    """给未来 Final Writer 的脱敏章节内容，不携带原始输入证据。"""

    model_config = ConfigDict(frozen=True)

    section: AnnualSectionKind
    status: SectionWorkStatus
    markdown: str | None = None
    citation_artifact_keys: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_content(self) -> "AnnualFinalizedSection":
        if self.status in {SectionWorkStatus.READY, SectionWorkStatus.PARTIAL}:
            if not self.markdown:
                raise ValueError("可交付章节必须包含 markdown")
        elif self.markdown is not None or self.citation_artifact_keys or not self.limitations:
            raise ValueError("不可交付章节不得携带内容或引用，且必须说明限制")
        return self


class AnnualFinalizationPack(BaseModel):
    """通过门禁后交给 Final Writer 的最小章节汇总包。"""

    model_config = ConfigDict(frozen=True)

    sections: tuple[AnnualFinalizedSection, ...] = Field(min_length=4, max_length=4)
    allowed_citation_artifact_keys: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_sections(self) -> "AnnualFinalizationPack":
        if {section.section for section in self.sections} != set(AnnualSectionKind):
            raise ValueError("最终汇总包必须且只能包含四类章节各一项")
        if len(set(self.allowed_citation_artifact_keys)) != len(
            self.allowed_citation_artifact_keys
        ):
            raise ValueError("最终汇总包不能包含重复引用工件键")
        cited = {
            key for section in self.sections for key in section.citation_artifact_keys
        }
        if cited != set(self.allowed_citation_artifact_keys):
            raise ValueError("最终汇总包的允许引用必须与章节实际引用完全一致")
        return self


class AnnualFinalizationDecision(BaseModel):
    """最终门禁输出：允许汇总、要求一次修订或拒绝。"""

    model_config = ConfigDict(frozen=True)

    status: AnnualFinalizationStatus
    issues: tuple[AnnualFinalizationIssue, ...] = ()
    finalization_pack: AnnualFinalizationPack | None = None
    revision_request: SectionRevisionRequest | None = None

    @model_validator(mode="after")
    def _validate_decision(self) -> "AnnualFinalizationDecision":
        if self.status in {
            AnnualFinalizationStatus.READY_FOR_FINAL_WRITER,
            AnnualFinalizationStatus.PARTIAL_READY_FOR_FINAL_WRITER,
        }:
            if self.finalization_pack is None or self.revision_request is not None:
                raise ValueError("可汇总决策必须包含汇总包且不得请求修订")
        elif self.status is AnnualFinalizationStatus.NEEDS_SECTION_REVISION:
            if self.revision_request is None or self.finalization_pack is not None:
                raise ValueError("修订决策必须包含修订请求且不得暴露汇总包")
        elif self.finalization_pack is not None or self.revision_request is not None:
            raise ValueError("blocked 决策不得包含汇总包或修订请求")
        return self


def finalized_section_from_draft(draft: SectionDraft) -> AnnualFinalizedSection:
    """剥离 SectionInputPack/AnalysisPack，只保留 Final Writer 需要的结果。"""

    return AnnualFinalizedSection(
        section=draft.section,
        status=draft.status,
        markdown=draft.markdown,
        citation_artifact_keys=draft.citation_artifact_keys,
        limitations=draft.limitations,
    )
