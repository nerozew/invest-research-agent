"""P03-15 纯 fake 端到端测试（不联网）。

验证目标（docs/05 P03-15、M3 收官）：
- ResearchFlow.run_fake 真实执行完整 00-07；
- state 六个业务字段全部填充：request/company_identity/research_pack/
  analysis_pack/report_draft/quality_report；
- fake draft 含 PRD §7 齐全章节 + 引用键 → 质量门禁通过 → run_manifest.status=published；
- state 可序列化往返（断点续跑基础）。

全程不联网：不调真实 SEC/搜索/LLM。
"""

from __future__ import annotations

from datetime import date

from invest_research.domain.models import ResearchRequest
from invest_research.flows.manifest import MANIFEST_VERSION
from invest_research.flows.research_flow import ResearchFlow
from invest_research.flows.state import ResearchFlowState


def test_e2e_fake_full_workflow_publishes() -> None:
    """端到端：请求 → 三 pack → 质量门禁通过 → manifest published。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)

    # 00-02：request / company_identity / research_pack
    assert state.request == request
    assert state.company_identity is not None
    assert state.company_identity.cik == "0000789019"
    assert state.research_pack is not None
    assert state.research_pack.as_of_date == date(2025, 12, 31)

    # 03：document_manifest
    assert "documents" in state.document_manifest

    # 04-05：analysis_pack / report_draft
    assert state.analysis_pack is not None
    assert state.analysis_pack.facts[0].concept == "Revenue"
    assert state.report_draft is not None
    assert state.report_draft.citation_keys  # 引用非空

    # 06：quality 通过
    assert state.quality_report is not None
    assert state.quality_report.all_passed is True
    assert state.quality_report.recommendation == "published"

    # 07：manifest published
    run_manifest = state.run_manifest
    assert isinstance(run_manifest, dict)
    assert run_manifest["status"] == "published"
    assert run_manifest["version"] == MANIFEST_VERSION


def test_e2e_fake_state_serializable_after_run() -> None:
    """执行后的 state 可序列化/恢复（断点续跑基础，业务字段一致）。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)
    restored = ResearchFlowState.model_validate_json(state.model_dump_json())

    assert restored.request == state.request
    assert restored.company_identity == state.company_identity
    assert restored.research_pack == state.research_pack
    assert restored.analysis_pack == state.analysis_pack
    assert restored.report_draft == state.report_draft
    assert restored.quality_report == state.quality_report
