"""P03-14 发布与 RunManifest 测试（纯函数 + Flow step07 集成，不联网）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.agents.llm_factory import LLMConfig
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
from invest_research.flows.manifest import MANIFEST_VERSION, build_run_manifest
from invest_research.flows.research_flow import ResearchFlow
from invest_research.flows.state import ResearchFlowState
from invest_research.settings import Settings


def _config() -> LLMConfig:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    return LLMConfig.from_settings(settings)


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


def _full_draft() -> ReportDraft:
    return ReportDraft(
        version="report_draft_v1",
        title="微软投资研究初稿",
        markdown=(
            "# 微软\n\n"
            "## 执行摘要\n摘要\n"
            "## 公司与业务概览\n概览\n"
            "## 近期重要事件与行业背景\n事件\n"
            "## 财务表现\n财务\n"
            "## 关键指标表\n指标\n"
            "## 风险因素与催化因素\n风险\n"
            "## 数据限制\n限制\n"
            "## 非投资建议声明\n非投资建议"
        ),
        citation_keys=["claim-1"],
    )


def _passing_state() -> ResearchFlowState:
    as_of = date(2025, 12, 31)
    return ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=as_of),
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        research_pack=_research_pack(as_of),
        analysis_pack=_analysis_pack(as_of),
        report_draft=_full_draft(),
        quality_report=QualityReport(
            version="quality_report_v1",
            all_passed=True,
            recommendation="published",
        ),
    )


def test_manifest_published_on_passing_quality() -> None:
    """quality 通过 → status=published，含版本/模型/prompt/pack checksum。"""
    manifest = build_run_manifest(_passing_state(), _config())
    assert manifest["status"] == "published"
    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["company"] == "0000789019"
    assert manifest["as_of_date"] == "2025-12-31"
    models = manifest["models"]
    assert isinstance(models, dict)
    assert models["research"] == "qwen-max"
    prompts = manifest["prompts"]
    assert isinstance(prompts, dict)
    assert "research_sha256" in prompts
    packs = manifest["packs"]
    assert isinstance(packs, dict)
    assert all(
        packs[k] for k in ("research_pack_sha256", "analysis_pack_sha256", "report_draft_sha256")
    )


def test_manifest_rejected_on_failed_quality() -> None:
    """quality 不通过 → status=rejected，reason 来自 recommendation。"""
    state = _passing_state()
    state.quality_report = QualityReport(
        version="quality_report_v1",
        all_passed=False,
        issues=["缺少必需章节"],
        recommendation="rejected",
    )
    manifest = build_run_manifest(state, _config())
    assert manifest["status"] == "rejected"
    assert manifest["reason"] == "rejected"


def test_manifest_requires_quality_report() -> None:
    """无 quality_report → rejected 且 reason=no_quality_report（防御）。"""
    state = _passing_state()
    state.quality_report = None
    manifest = build_run_manifest(state, _config())
    assert manifest["status"] == "rejected"
    assert manifest["reason"] == "no_quality_report"


def test_pack_checksum_is_stable() -> None:
    """同 pack → 同 checksum（可复现校验）。"""
    m1 = build_run_manifest(_passing_state(), _config())
    m2 = build_run_manifest(_passing_state(), _config())
    p1, p2 = m1["packs"], m2["packs"]
    assert isinstance(p1, dict) and isinstance(p2, dict)
    assert p1["report_draft_sha256"] == p2["report_draft_sha256"]


def test_flow_step07_writes_run_manifest() -> None:
    """Flow 集成：run_fake 后 state.run_manifest 已被 step07 填充（合规 → published）。"""
    flow = ResearchFlow()
    request = ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))
    state = flow.run_fake(request)
    # fake draft 含 PRD §7 必备章节 + 引用键 → 质量门禁通过 → manifest published
    run_manifest = state.run_manifest
    assert isinstance(run_manifest, dict)
    assert run_manifest["status"] == "published"
    assert run_manifest["version"] == MANIFEST_VERSION
