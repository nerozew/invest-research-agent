"""P03-21 纯 fake 反思闭环 E2E（不联网，覆盖发布/修订/补证/拒绝/partial/上限）。

验证目标（docs/05 P03-21）：
1. 第一次检查直接发布；
2. Writer 修订一次后发布；
3. 补证一次后重新分析并发布；
4. 只有非关键警告时 partial；
5. 修订后仍有关键问题时 rejected；
6. 达到次数上限后停止（repeat_reject）；
7. 全程无真实网络和模型调用（纯 fake：确定性修订 / 确定性补证）；
8. 每轮产物、原因和次数可审计（ReflectionController.history + state 产物）。
"""

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
from invest_research.domain.quality import QualityAction, QualityIssue, QualitySeverity
from invest_research.flows.quality_classifier import classify_state
from invest_research.flows.reflection import ReflectionController
from invest_research.flows.state import ResearchFlowState

_FULL_MD = (
    "# 微软\n\n"
    "## 执行摘要\n摘要\n"
    "## 公司与业务概览\n概览\n"
    "## 财务表现\n财务\n"
    "## 风险\n风险\n"
    "## 数据限制\n限制\n"
    "## 非投资建议\n声明"
)


def _draft(md: str = _FULL_MD, keys: list[str] | None = None) -> ReportDraft:
    return ReportDraft(
        version="report_draft_v1",
        title="微软投资研究初稿",
        markdown=md,
        citation_keys=keys if keys is not None else ["c1"],
    )


def _base_state(md: str = _FULL_MD, keys: list[str] | None = None) -> ResearchFlowState:
    as_of = date(2025, 12, 31)
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    return ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=as_of),
        company_identity=identity,
        research_pack=ResearchPack(
            version="research_pack_v1",
            company_identity=identity,
            as_of_date=as_of,
            sources=[
                Source(
                    source_type=SourceType.SEC_FILING,
                    canonical_url="https://e.com/10k",
                    title="10-K",
                    accessed_at=as_of,
                )
            ],
        ),
        analysis_pack=FinancialAnalysisPack(
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
                    period_start=date(2024, 7, 1),
                    period_end=as_of,
                )
            ],
        ),
        report_draft=_draft(md, keys),
    )


def _issues_to_action(issues: list[QualityIssue]) -> QualityAction:
    """聚合：CRITICAL→REJECT；REVISE_REPORT→REVISE；否则 NONE。"""
    if any(i.severity == QualitySeverity.CRITICAL for i in issues):
        return QualityAction.REJECT
    if any(i.action == QualityAction.REVISE_REPORT for i in issues):
        return QualityAction.REVISE_REPORT
    return QualityAction.NONE


def test_e2e_publish_first_pass() -> None:
    """场景1：第一次检查直接发布。"""
    state = _base_state()
    ctrl = ReflectionController()
    step = ctrl.step(_issues_to_action(classify_state(state)), 0, 0)
    assert step.outcome == "publish"
    assert step.revision_used == 0 and step.supplement_used == 0


def test_e2e_revise_once_then_publish() -> None:
    """场景2：修订一次后发布。"""
    incomplete = "# 微软\n\n> 非投资建议"
    state = _base_state(md=incomplete, keys=[])
    ctrl = ReflectionController()
    step1 = ctrl.step(_issues_to_action(classify_state(state)), 0, 0)
    assert step1.outcome == "revise"
    # fake 修订：补全章节与引用
    state.report_draft = _draft()
    step2 = ctrl.step(_issues_to_action(classify_state(state)), step1.revision_used, 0)
    assert step2.outcome == "publish"
    assert step2.revision_used == 1


def test_e2e_supplement_once_then_publish() -> None:
    """场景3：补证一次后发布（补证来源可审计、来源数 +1）。"""
    state = _base_state()
    ctrl = ReflectionController()
    step1 = ctrl.step(QualityAction.SUPPLEMENT_RESEARCH, 0, 0)
    assert step1.outcome == "supplement"
    assert step1.supplement_used == 1
    # 确定性补证：追加 fake 来源
    assert state.research_pack is not None
    state.research_pack = state.research_pack.model_copy(
        update={
            "sources": [
                *state.research_pack.sources,
                Source(
                    source_type=SourceType.SEC_FILING,
                    canonical_url="https://supp.example/extra",
                    title="supp",
                    accessed_at=date(2025, 12, 31),
                ),
            ]
        }
    )
    assert len(state.research_pack.sources) == 2
    step2 = ctrl.step(_issues_to_action(classify_state(state)), 0, step1.supplement_used)
    assert step2.outcome == "publish"


def test_e2e_partial_on_warnings() -> None:
    """场景4：仅非关键警告 → publish_partial。"""
    ctrl = ReflectionController()
    step = ctrl.step(QualityAction.NONE, 0, 0, has_warnings=True)
    assert step.outcome == "publish_partial"


def test_e2e_reject_after_critical() -> None:
    """场景5：出现 CRITICAL（如目标价）→ rejected（不可修复）。"""
    state = _base_state(md=_FULL_MD + "\n\n目标价 100 美元")
    ctrl = ReflectionController()
    issues = classify_state(state)
    assert any(i.severity == QualitySeverity.CRITICAL for i in issues)
    step = ctrl.step(_issues_to_action(issues), 0, 0)
    assert step.outcome == "reject"
    assert step.revision_used == 0


def test_e2e_repeat_hits_limit_stops() -> None:
    """场景6：达到次数上限停止（不陷入无限循环）。"""
    ctrl = ReflectionController()
    first = ctrl.step(QualityAction.REVISE_REPORT, 0, 0)
    second = ctrl.step(QualityAction.REVISE_REPORT, first.revision_used, 0)
    assert second.outcome == "repeat_reject"
    assert len(ctrl.history) == 2
