"""P03-10 typed Flow state 测试（ResearchFlowState，Pydantic）。

验证目标（docs/05 P03-10 验收）：
- 默认值：全 None / 空容器（新建状态即为"未开始"）；
- 序列化/恢复：model_dump_json → model_validate_json 往返无损（Flow 断点续跑基础）；
- 类型校验：packs 字段类型正确、非法类型被拒；
- 可修改：Flow 运行期可给 self.state 的字段赋值（非 frozen）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

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
from invest_research.flows import ResearchFlowState


def test_default_state_is_empty() -> None:
    """新建状态全空（None / 空容器），表示任务未开始。"""
    state = ResearchFlowState()
    assert state.request is None
    assert state.company_identity is None
    assert state.research_pack is None
    assert state.document_manifest == {}
    assert state.analysis_pack is None
    assert state.report_draft is None
    assert state.quality_report is None
    assert state.run_manifest == {}


def test_state_serialize_and_recover_roundtrip() -> None:
    """序列化 → 反序列化 往返无损（Flow 断点续跑的地基）。"""
    state = ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31)),
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        document_manifest={"count": 2},
    )
    restored = ResearchFlowState.model_validate_json(state.model_dump_json())

    assert restored == state
    assert restored.request is not None
    assert restored.request.input_company == "MSFT"
    assert restored.document_manifest == {"count": 2}


def test_state_accepts_full_packs() -> None:
    """三个 Agent pack + 质量报告都能放进 state（配合 Crew 产出）。"""
    pack = ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        as_of_date=date(2025, 12, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/10k",
                title="10-K",
                accessed_at=date(2025, 12, 31),
            )
        ],
    )
    state = ResearchFlowState(research_pack=pack)
    assert state.research_pack is not None
    assert state.research_pack.version == "research_pack_v1"


def test_state_flow_can_mutate_fields() -> None:
    """Flow 运行期能赋值（非 frozen）——@start/@listen 里 self.state.x = ..."""
    state = ResearchFlowState()
    state.request = ResearchRequest(input_company="AAPL", as_of_date=date(2025, 6, 30))
    assert state.request is not None
    assert state.request.input_company == "AAPL"


def test_state_rejects_wrong_type() -> None:
    """类型校验：company_identity 不能塞字符串。"""
    with pytest.raises(ValidationError):
        ResearchFlowState(company_identity="not-a-model")


def test_state_with_analysis_and_report() -> None:
    """analysis + report + quality 字段可用（供后续 Crew 步骤）。"""
    fact = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="Revenue",
        value=Decimal("100"),
        unit="USD",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1", period_end=date(2025, 12, 31), facts=[fact]
    )
    draft = ReportDraft(
        version="report_draft_v1", title="标题", markdown="# 正文", citation_keys=[]
    )
    quality = QualityReport(
        version="quality_report_v1", all_passed=True, recommendation="published"
    )
    state = ResearchFlowState(analysis_pack=analysis, report_draft=draft, quality_report=quality)
    assert state.analysis_pack is not None
    assert state.report_draft is not None
    assert state.quality_report is not None
    assert state.analysis_pack.facts[0].concept == "Revenue"
    assert state.report_draft.title == "标题"
    assert state.quality_report.all_passed is True
