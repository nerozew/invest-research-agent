"""P03-11 00-03 Flow 步骤测试（fake 逻辑，不联网）。

验证目标（docs/05 P03-11）：
- step00-03 按 @start/@listen 严格顺序推进，state 逐字段填充；
- 请求缺省时 step00 抛 ValueError（fail-fast）；
- run_fake 便捷入口可注入请求、kickoff 后返回执行完毕的状态；
- 每个步骤的产出写入正确 field（request/company_identity/research_pack/document_manifest）。
"""

from __future__ import annotations

from datetime import date

import pytest

from invest_research.domain.models import ResearchRequest
from invest_research.flows.research_flow import ResearchFlow
from invest_research.flows.state import ResearchFlowState


def test_flow_runs_all_steps_in_order() -> None:
    """run_fake：注入请求后 kickoff，00-03 全部推进并填充 state。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)

    # step00 request 保留
    assert state.request == request
    # step01 company_identity 填充
    assert state.company_identity is not None
    assert state.company_identity.cik == "0000789019"
    assert state.company_identity.legal_name == "MSFT Corp"
    # step02 research_pack 填充（引用 company_identity / as_of_date）
    assert state.research_pack is not None
    assert state.research_pack.company_identity == state.company_identity
    assert state.research_pack.as_of_date == date(2025, 12, 31)
    # step03 document_manifest 填充
    docs = state.document_manifest.get("documents")
    assert isinstance(docs, list)
    first = docs[0]
    assert isinstance(first, dict)
    assert first.get("filing") == "10-K"


def test_flow_requires_request() -> None:
    """step00 前无 request → 抛 ValueError（fail-fast）。"""
    flow = ResearchFlow()
    with pytest.raises(ValueError):
        flow.kickoff()


def test_flow_resolves_unknown_company_as_generic_identity() -> None:
    """非 MSFT 公司：fake 解析成通用 CIK（0000000000），流程仍走通。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="AAPL", as_of_date=date(2025, 6, 30))
    state = flow.run_fake(request)

    assert state.company_identity is not None
    assert state.company_identity.cik == "0000000000"
    assert state.research_pack is not None


def test_flow_state_serializable_after_run() -> None:
    """执行后的 state 可序列化/恢复（断点续跑基础）。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)

    restored = ResearchFlowState.model_validate_json(state.model_dump_json())
    # Flow 会给 state 注入运行时 id（StateWithId），业务字段比较即可
    assert restored.request == state.request
    assert restored.company_identity == state.company_identity
    assert restored.research_pack == state.research_pack
    assert restored.document_manifest == state.document_manifest


def test_flow_runs_full_chain_00_to_05() -> None:
    """P03-12：00-05 线性链——research→analysis→draft 全部填入 state。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)

    # step04：analysis_pack 已填充（财务分析 Agent 产物）
    assert state.analysis_pack is not None
    assert state.analysis_pack.version == "analysis_pack_v1"
    assert state.analysis_pack.facts[0].concept == "Revenue"
    # step05：report_draft 已填充（报告撰写 Agent 产物）
    assert state.report_draft is not None
    assert state.report_draft.version == "report_draft_v1"
    assert "MSFT" in state.report_draft.title
    assert "非投资建议" in state.report_draft.markdown


def test_flow_runs_full_chain_00_to_06() -> None:
    """P03-13：step06 质量门禁被触发——quality_report 写入 state。

    fake draft 含 PRD §7 必需章节 + 引用键 → all_passed=True、published。
    这验证门禁对合规产出放行（而非误拦截）。
    """
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)

    # step06：quality_report 已填充
    assert state.quality_report is not None
    assert state.quality_report.version == "quality_report_v1"
    # fake draft 合规 → 门禁放行
    assert state.quality_report.all_passed is True
    assert state.quality_report.recommendation == "published"
    assert state.quality_report.issues == []
