"""P03-17 结构化质量分类测试（纯函数，不联网）。

验证目标（docs/05 P03-17）：
- classify_state 对空 pack/不一致/as_of/缺章节/缺引用/禁止建议输出结构化 QualityIssue；
- recommendation_from_issues 正确区分 CRITICAL→REJECT / ERROR→REVISE /
  仅警告→PUBLISH_PARTIAL / 无→PUBLISH；
- run_quality_gate（P03-13 门禁）复用 classifier 后仍产生 QualityReport（兼容布尔输出）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    QualityReport,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.domain.quality import QualityAction, QualityRecommendation, QualitySeverity
from invest_research.flows.quality import run_quality_gate
from invest_research.flows.quality_classifier import (
    all_passed_from_issues,
    classify_state,
    recommendation_from_issues,
)
from invest_research.flows.state import ResearchFlowState


def _pack(as_of: date) -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://e.com/10k",
                title="10-K",
                accessed_at=as_of,
            )
        ],
    )


def _analysis(as_of: date) -> FinancialAnalysisPack:
    return FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="c1",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=Decimal("100"),
                unit="USD",
                period_start=date(as_of.year - 1, 7, 1),
                period_end=as_of,
            )
        ],
    )


def _draft(complete: bool = True, forbidden: str = "") -> ReportDraft:
    md = (
        "# 微软\n\n"
        "## 执行摘要\n\n"
        "## 公司与业务概览\n\n"
        "## 财务表现\n\n"
        "## 风险\n\n"
        "## 数据限制\n\n"
        "## 非投资建议\n\n"
    )
    if not complete:
        md = "# 微软\n\n> 非投资建议"
    if forbidden:
        md = f"{md}\n\n目标价 100"
    return ReportDraft(
        version="report_draft_v1",
        title="t",
        markdown=md,
        citation_keys=[] if not complete else ["c1"],
    )


def _state(**kw: object) -> ResearchFlowState:
    as_of = date(2025, 12, 31)
    base: dict[str, object] = {
        "request": ResearchRequest(input_company="MSFT", as_of_date=as_of),
        "company_identity": CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        "research_pack": _pack(as_of),
        "analysis_pack": _analysis(as_of),
        "report_draft": _draft(complete=True),
    }
    base.update(kw)
    return ResearchFlowState(**base)  # type: ignore[arg-type]


def test_classify_clean_publishes() -> None:
    issues = classify_state(_state())
    assert issues == []
    assert recommendation_from_issues(issues, []) == QualityRecommendation.PUBLISH
    assert all_passed_from_issues(issues) is True


def test_classify_incomplete_draft_revise() -> None:
    issues = classify_state(_state(report_draft=_draft(complete=False)))
    assert any(i.action == QualityAction.REVISE_REPORT for i in issues)
    assert recommendation_from_issues(issues, []) == QualityRecommendation.REVISE
    assert all_passed_from_issues(issues) is False


def test_classify_forbidden_advice_reject() -> None:
    issues = classify_state(_state(report_draft=_draft(complete=True, forbidden="目标价")))
    assert any(
        i.action == QualityAction.REJECT and i.severity == QualitySeverity.CRITICAL for i in issues
    )
    assert recommendation_from_issues(issues, []) == QualityRecommendation.REJECT


def test_classify_missing_pack_reject() -> None:
    issues = classify_state(_state(research_pack=None))
    assert any("research_pack" in i.message for i in issues)
    assert recommendation_from_issues(issues, []) == QualityRecommendation.REJECT


def test_warning_yields_partial() -> None:
    issues: list = []
    rec = recommendation_from_issues(issues, ["非关键来源缺失"])
    assert rec == QualityRecommendation.PUBLISH_PARTIAL


def test_run_quality_gate_compat() -> None:
    report = run_quality_gate(_state())
    assert isinstance(report, QualityReport)
    assert report.all_passed is True
    assert report.recommendation == QualityRecommendation.PUBLISH

    bad = run_quality_gate(_state(report_draft=_draft(complete=False)))
    assert bad.all_passed is False
    assert any("执行摘要" in m for m in bad.issues)

def test_period_end_after_as_of_is_rejected() -> None:
    """period_end 晚于 as_of_date → period_end_mismatch（CRITICAL，禁止用未来数据）。"""
    as_of = date(2025, 10, 31)
    state = ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=as_of),
        research_pack=_pack(as_of),
        analysis_pack=_analysis(date(2025, 12, 31)),  # 晚于 as_of
        report_draft=_draft(),
    )
    issues = classify_state(state)
    assert "period_end_mismatch" in [i.code for i in issues]


def test_period_end_before_as_of_is_allowed() -> None:
    """period_end 早于 as_of_date（如财年结束日）不触发 mismatch（P05.5-fix）。"""
    as_of = date(2025, 10, 31)
    state = ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=as_of),
        research_pack=_pack(as_of),
        analysis_pack=_analysis(date(2025, 9, 27)),  # 财年结束日 < as_of
        report_draft=_draft(),
    )
    issues = classify_state(state)
    assert all(i.code != "period_end_mismatch" for i in issues)

