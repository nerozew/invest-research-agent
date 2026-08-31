"""P07-09：年度章节的确定性最终汇总门禁，不调用模型或外部服务。"""

from __future__ import annotations

from collections.abc import Iterable

from invest_research.domain.annual_finalization import (
    AnnualFinalizationDecision,
    AnnualFinalizationIssue,
    AnnualFinalizationPack,
    AnnualFinalizationStatus,
    SectionRevisionRequest,
    finalized_section_from_draft,
)
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    SectionDraft,
    SectionWorkStatus,
)

_CORE_SECTIONS = (
    AnnualSectionKind.FINANCIAL_PERFORMANCE,
    AnnualSectionKind.BUSINESS_OVERVIEW,
    AnnualSectionKind.RISK_FACTORS,
)
_SECTION_ORDER = (*_CORE_SECTIONS, AnnualSectionKind.MATERIAL_EVENTS)


class AnnualFinalizationGate:
    """检查章节完整性并在必要时只发出一次可修复的定向请求。"""

    @classmethod
    def evaluate(
        cls,
        drafts: Iterable[SectionDraft],
        *,
        revision_attempt: int = 0,
    ) -> AnnualFinalizationDecision:
        drafts_by_section, structural_issues = cls._index(tuple(drafts))
        if structural_issues:
            return cls._blocked(structural_issues)

        core_blockers = cls._core_blockers(drafts_by_section)
        if core_blockers:
            return cls._blocked(core_blockers)

        revisable_issues = cls._revisable_issues(drafts_by_section)
        if revisable_issues:
            if revision_attempt >= 1:
                return cls._blocked(
                    tuple(
                        (*revisable_issues, cls._revision_exhausted_issue())
                    )
                )
            return AnnualFinalizationDecision(
                status=AnnualFinalizationStatus.NEEDS_SECTION_REVISION,
                issues=revisable_issues,
                revision_request=SectionRevisionRequest(
                    revision_number=1,
                    target_sections=tuple(
                        dict.fromkeys(
                            issue.section
                            for issue in revisable_issues
                            if issue.section is not None
                        )
                    ),
                    issues=revisable_issues,
                ),
            )

        ordered_drafts = tuple(drafts_by_section[section] for section in _SECTION_ORDER)
        finalized = tuple(finalized_section_from_draft(draft) for draft in ordered_drafts)
        limitations = cls._unique(
            limitation for draft in ordered_drafts for limitation in cls._limitations(draft)
        )
        citations = cls._unique(
            key for draft in ordered_drafts for key in draft.citation_artifact_keys
        )
        pack = AnnualFinalizationPack(
            sections=finalized,
            allowed_citation_artifact_keys=citations,
            limitations=limitations,
        )
        has_partial_core = any(
            drafts_by_section[section].status is SectionWorkStatus.PARTIAL
            for section in _CORE_SECTIONS
        )
        event_blocked = (
            drafts_by_section[AnnualSectionKind.MATERIAL_EVENTS].status
            is SectionWorkStatus.BLOCKED
        )
        status = (
            AnnualFinalizationStatus.PARTIAL_READY_FOR_FINAL_WRITER
            if has_partial_core or event_blocked
            else AnnualFinalizationStatus.READY_FOR_FINAL_WRITER
        )
        return AnnualFinalizationDecision(status=status, finalization_pack=pack)

    @staticmethod
    def _index(
        drafts: tuple[SectionDraft, ...],
    ) -> tuple[dict[AnnualSectionKind, SectionDraft], tuple[AnnualFinalizationIssue, ...]]:
        by_section: dict[AnnualSectionKind, SectionDraft] = {}
        issues: list[AnnualFinalizationIssue] = []
        for draft in drafts:
            if draft.section in by_section:
                issues.append(
                    AnnualFinalizationIssue(
                        code="duplicate_section",
                        message=f"章节 {draft.section.value} 出现重复草稿",
                        section=draft.section,
                    )
                )
            by_section[draft.section] = draft
        for section in _SECTION_ORDER:
            if section not in by_section:
                issues.append(
                    AnnualFinalizationIssue(
                        code="missing_section",
                        message=f"缺少必要章节草稿：{section.value}",
                        section=section,
                    )
                )
        return by_section, tuple(issues)

    @staticmethod
    def _core_blockers(
        drafts: dict[AnnualSectionKind, SectionDraft],
    ) -> tuple[AnnualFinalizationIssue, ...]:
        issues: list[AnnualFinalizationIssue] = []
        for section in _CORE_SECTIONS:
            draft = drafts[section]
            if draft.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
                issues.append(
                    AnnualFinalizationIssue(
                        code="core_section_unavailable",
                        message=f"核心章节不可交付：{section.value}",
                        section=section,
                    )
                )
        return tuple(issues)

    @staticmethod
    def _revisable_issues(
        drafts: dict[AnnualSectionKind, SectionDraft],
    ) -> tuple[AnnualFinalizationIssue, ...]:
        issues: list[AnnualFinalizationIssue] = []
        for section in _CORE_SECTIONS:
            draft = drafts[section]
            if not draft.citation_artifact_keys:
                issues.append(
                    AnnualFinalizationIssue(
                        code="missing_section_citation",
                        message=f"核心章节缺少可追溯引用：{section.value}",
                        section=section,
                        revisable=True,
                    )
                )
            if (
                draft.status is SectionWorkStatus.PARTIAL
                and not AnnualFinalizationGate._limitations(draft)
            ):
                issues.append(
                    AnnualFinalizationIssue(
                        code="partial_without_limitation",
                        message=f"部分章节未披露限制：{section.value}",
                        section=section,
                        revisable=True,
                    )
                )
        return tuple(issues)

    @staticmethod
    def _revision_exhausted_issue() -> AnnualFinalizationIssue:
        return AnnualFinalizationIssue(
            code="section_revision_exhausted",
            message="章节定向修订额度已耗尽，禁止继续循环",
        )

    @staticmethod
    def _limitations(draft: SectionDraft) -> tuple[str, ...]:
        if draft.section is AnnualSectionKind.FINANCIAL_PERFORMANCE:
            if draft.financial_analysis is None:
                return draft.limitations
            return (
                *draft.limitations,
                *draft.financial_analysis.limitations,
                *draft.financial_analysis.section_input.limitations,
            )
        if draft.section_input is None:
            return draft.limitations
        return (*draft.limitations, *draft.section_input.limitations)

    @staticmethod
    def _unique(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _blocked(
        issues: tuple[AnnualFinalizationIssue, ...],
    ) -> AnnualFinalizationDecision:
        return AnnualFinalizationDecision(
            status=AnnualFinalizationStatus.BLOCKED,
            issues=issues,
        )
