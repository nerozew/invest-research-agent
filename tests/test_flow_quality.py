"""P03-13 质量门禁测试（纯函数，不联网）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.flows.quality import run_quality_gate
from invest_research.flows.state import ResearchFlowState


def _research_pack(as_of: date) -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/10k",
                title="10-K",
                accessed_at=as_of,
            )
        ],
    )


def _analysis_pack(as_of: date) -> FinancialAnalysisPack:
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


def _draft_with(all_sections: bool = True, keys: list[str] | None = None) -> ReportDraft:
    md = (
        "# 微软\n\n"
        "## 执行摘要\n摘要\n"
        "## 公司与业务概览\n概览\n"
        "## 近期重要事件与行业背景\n事件\n"
        "## 财务表现\n财务\n"
        "## 关键指标表\n指标\n"
        "## 风险因素与催化因素\n风险\n"
        "## 数据限制\n限制\n"
        "## 非投资建议声明\n非投资建议"
    )
    if not all_sections:
        md = "# 微软\n\n> 非投资建议"
    return ReportDraft(
        version="report_draft_v1",
        title="微软投资研究初稿",
        markdown=md,
        citation_keys=keys if keys is not None else ["claim-1"],
    )


def _state(**overrides: object) -> ResearchFlowState:
    as_of = date(2025, 12, 31)
    base: dict[str, object] = {
        "request": ResearchRequest(input_company="MSFT", as_of_date=as_of),
        "company_identity": CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        "research_pack": _research_pack(as_of),
        "analysis_pack": _analysis_pack(as_of),
        "report_draft": _draft_with(),
    }
    base.update(overrides)
    return ResearchFlowState.model_validate(base)


def test_all_passes_publishes() -> None:
    """完整合规 state → all_passed=True、recommendation=published。"""
    report = run_quality_gate(_state())
    assert report.all_passed is True
    assert report.recommendation == "published"
    assert report.issues == []


def test_missing_packs_are_flagged() -> None:
    """缺 research/analysis/report → issues 列明。"""
    report = run_quality_gate(_state(research_pack=None, analysis_pack=None, report_draft=None))
    assert report.all_passed is False
    assert any("research_pack" in i for i in report.issues)
    assert any("analysis_pack" in i for i in report.issues)
    assert any("report_draft" in i for i in report.issues)


def test_incomplete_draft_rejected() -> None:
    """缺必需章节 + 空引用 → 判定有问题。"""
    report = run_quality_gate(_state(report_draft=_draft_with(all_sections=False, keys=[])))
    assert report.all_passed is False
    assert any("执行摘要" in i for i in report.issues)
    assert any("citation_keys" in i for i in report.issues)


def test_as_of_mismatch_flagged() -> None:
    """research_pack.as_of_date 与 request 不一致 → issue。"""
    state = _state()
    # request 用默认 as_of(2025-12-31)；改 research_pack 为 2024 触发不一致
    state.research_pack = _research_pack(date(2024, 12, 31))
    report = run_quality_gate(state)
    assert report.all_passed is False
    assert any("as_of_date" in i for i in report.issues)


def test_forbidden_investment_advice_flagged() -> None:
    """报告含'目标价'→ issue。"""
    draft = _draft_with()
    draft = draft.model_copy(update={"markdown": draft.markdown + "\n\n目标价 100 美元"})
    report = run_quality_gate(_state(report_draft=draft))
    assert report.all_passed is False
    assert any("目标价" in i for i in report.issues)
