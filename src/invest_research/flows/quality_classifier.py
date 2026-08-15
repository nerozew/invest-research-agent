"""P03-17 结构化质量问题分类（quality classifier，确定性）。

把质量门禁升级为「QualityIssue 列表 + QualityRecommendation」的分类，
供 P03-18/19/20 定向修订/补证/受控路由使用。纯函数，不依赖 CrewAI。
"""

from __future__ import annotations

from invest_research.domain.quality import (
    QualityAction,
    QualityIssue,
    QualityRecommendation,
    QualitySeverity,
)
from invest_research.flows.state import ResearchFlowState

REQUIRED_SECTIONS: tuple[str, ...] = (
    "执行摘要",
    "公司与业务概览",
    "财务表现",
    "风险",
    "数据限制",
    "非投资建议",
)

FORBIDDEN_PATTERNS: tuple[str, ...] = ("买入", "卖出", "目标价", "建议持仓")


def _issue(
    code: str,
    severity: QualitySeverity,
    stage: str,
    message: str,
    action: QualityAction,
) -> QualityIssue:
    return QualityIssue(code=code, severity=severity, stage=stage, message=message, action=action)


def classify_state(state: ResearchFlowState) -> list[QualityIssue]:
    """对 00-05 产物输出结构化 QualityIssue（确定性）。"""
    issues: list[QualityIssue] = []

    if state.research_pack is None:
        issues.append(
            _issue(
                "missing_research_pack",
                QualitySeverity.CRITICAL,
                "research",
                "缺少 research_pack",
                QualityAction.REJECT,
            )
        )
    if state.analysis_pack is None:
        issues.append(
            _issue(
                "missing_analysis_pack",
                QualitySeverity.CRITICAL,
                "analysis",
                "缺少 analysis_pack",
                QualityAction.REJECT,
            )
        )
    if state.report_draft is None:
        issues.append(
            _issue(
                "missing_report_draft",
                QualitySeverity.CRITICAL,
                "writer",
                "缺少 report_draft",
                QualityAction.REJECT,
            )
        )

    if (
        state.research_pack is not None
        and state.request is not None
        and state.research_pack.as_of_date != state.request.as_of_date
    ):
        issues.append(
            _issue(
                "as_of_mismatch",
                QualitySeverity.CRITICAL,
                "research",
                "as_of_date 不一致",
                QualityAction.REJECT,
            )
        )
    if (
        state.analysis_pack is not None
        and state.research_pack is not None
        and state.analysis_pack.period_end > state.research_pack.as_of_date
    ):
        # P05.5-fix：分析的是财年/季度期间，period_end 是期间截止日（如 AAPL FY2025
        # 截至 2025-09-27），只需不晚于数据截止日 as_of_date（禁止使用截止日之后数据），
        # 不应与 as_of_date 精确相等。
        issues.append(
            _issue(
                "period_end_mismatch",
                QualitySeverity.CRITICAL,
                "analysis",
                "period_end 晚于 as_of_date（禁止使用截止日之后的数据）",
                QualityAction.REJECT,
            )
        )

    if state.report_draft is not None:
        md = state.report_draft.markdown
        for section in REQUIRED_SECTIONS:
            if section not in md:
                issues.append(
                    _issue(
                        "missing_section",
                        QualitySeverity.ERROR,
                        "writer",
                        f"缺少必需章节: {section}",
                        QualityAction.REVISE_REPORT,
                    )
                )
        if not state.report_draft.citation_keys:
            issues.append(
                _issue(
                    "missing_citation_keys",
                    QualitySeverity.ERROR,
                    "writer",
                    "citation_keys 为空",
                    QualityAction.REVISE_REPORT,
                )
            )
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in md:
                issues.append(
                    _issue(
                        "forbidden_advice",
                        QualitySeverity.CRITICAL,
                        "writer",
                        f"禁止的投资建议: {pattern}",
                        QualityAction.REJECT,
                    )
                )

    return issues


def recommendation_from_issues(
    issues: list[QualityIssue], warnings: list[str]
) -> QualityRecommendation:
    """聚合最终建议：CRITICAL→REJECT；ERROR→REVISE；仅警告→PUBLISH_PARTIAL；无→PUBLISH。"""
    if any(i.severity == QualitySeverity.CRITICAL for i in issues):
        return QualityRecommendation.REJECT
    if any(i.action == QualityAction.REVISE_REPORT for i in issues):
        return QualityRecommendation.REVISE
    if warnings:
        return QualityRecommendation.PUBLISH_PARTIAL
    return QualityRecommendation.PUBLISH


def all_passed_from_issues(issues: list[QualityIssue]) -> bool:
    """可直接发布（无 ERROR/CRITICAL，PUBLISH_PARTIAL 视为可发布）。"""
    return not any(i.severity in (QualitySeverity.ERROR, QualitySeverity.CRITICAL) for i in issues)
