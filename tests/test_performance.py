"""P05.5-1 性能记录单元测试（PerformanceRecorder / extract_token_usage / manifest.performance）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from crewai.types.usage_metrics import UsageMetrics

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
from invest_research.flows.manifest import build_run_manifest
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.flow_wiring import LiveResearchFlowRunner
from invest_research.infrastructure.performance import PerformanceRecorder, extract_token_usage
from invest_research.settings import Settings


def _config() -> LLMConfig:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    return LLMConfig.from_settings(settings)


def _passing_state(as_of: date) -> ResearchFlowState:
    return ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=as_of),
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        research_pack=ResearchPack(
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
        ),
        analysis_pack=FinancialAnalysisPack(
            version="analysis_pack_v1",
            period_end=as_of,
            facts=[
                FinancialFact(
                    company_id="0000789019",
                    source_id="s1",
                    taxonomy="us-gaap",
                    concept="Revenue",
                    value=100,
                    unit="USD",
                    period_start=date(as_of.year - 1, 7, 1),
                    period_end=as_of,
                )
            ],
        ),
        report_draft=ReportDraft(
            version="report_draft_v1",
            title="Microsoft Corp 投资研究初稿",
            markdown="# Microsoft Corp\n\n## 执行摘要\n内容",
            citation_keys=["claim-1"],
        ),
        quality_report=QualityReport(
            version="quality_report_v1",
            all_passed=True,
            recommendation="published",
        ),
    )


# ---- PerformanceRecorder ----


def test_recorder_times_tool_and_counts() -> None:
    recorder = PerformanceRecorder()
    with recorder.timed_tool("sec_submissions"):
        pass
    with recorder.timed_tool("sec_submissions"):
        pass
    with recorder.timed_tool("web_search"):
        pass
    snap = recorder.snapshot()
    tools = snap["tools"]
    assert tools["sec_submissions"]["calls"] == 2
    assert tools["web_search"]["calls"] == 1
    assert tools["sec_submissions"]["total_ms"] >= 0.0


def test_recorder_records_agent_duration() -> None:
    recorder = PerformanceRecorder()
    recorder.record_agent("research", 1234)
    snap = recorder.snapshot()
    assert snap["agents"]["research"] == {"duration_ms": 1234}


def test_recorder_records_cache_hit_and_token_usage() -> None:
    recorder = PerformanceRecorder()
    recorder.record_cache_hit("sec_submissions")
    recorder.set_token_usage({"prompt_tokens": 10, "total_tokens": 15})
    snap = recorder.snapshot()
    assert snap["cache_hits"]["sec_submissions"] == 1
    assert snap["token_usage"]["total_tokens"] == 15


def test_recorder_default_token_usage_is_none() -> None:
    assert PerformanceRecorder().snapshot()["token_usage"] is None


# ---- extract_token_usage ----


def test_extract_token_usage_none_or_zero() -> None:
    assert extract_token_usage(None) is None
    assert extract_token_usage({"total_tokens": 0}) is None
    assert extract_token_usage(UsageMetrics()) is None


def test_extract_token_usage_from_usage_metrics() -> None:
    usage = UsageMetrics(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cached_prompt_tokens=20,
        successful_requests=2,
    )
    out = extract_token_usage(usage)
    assert out is not None
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 50
    assert out["total_tokens"] == 150
    assert out["cached_prompt_tokens"] == 20
    assert out["successful_requests"] == 2


def test_extract_token_usage_from_dict_fills_missing() -> None:
    out = extract_token_usage({"total_tokens": 42, "prompt_tokens": 10, "completion_tokens": 32})
    assert out is not None
    assert out["total_tokens"] == 42
    assert out["cached_prompt_tokens"] == 0


# ---- manifest.performance ----


def test_manifest_published_includes_performance() -> None:
    perf = {"agents": {"research": {"duration_ms": 1}}, "tools": {}, "token_usage": None}
    manifest = build_run_manifest(_passing_state(date(2025, 12, 31)), _config(), performance=perf)
    assert manifest["status"] == "published"
    assert manifest["performance"] == perf


def test_manifest_rejected_includes_performance() -> None:
    state = ResearchFlowState(
        request=ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31)),
        quality_report=QualityReport(
            version="quality_report_v1", all_passed=False, recommendation="rejected"
        ),
    )
    perf = {"agents": {}, "tools": {}, "token_usage": None}
    manifest = build_run_manifest(state, _config(), performance=perf)
    assert manifest["status"] == "rejected"
    assert manifest["performance"] == perf


def test_manifest_performance_defaults_to_empty() -> None:
    manifest = build_run_manifest(_passing_state(date(2025, 12, 31)), _config())
    assert manifest["performance"] == {}


# ---- live runner 性能采集（离线 fake crew）----


def test_live_runner_writes_performance_to_manifest(tmp_path_factory) -> None:
    as_of = date(2025, 12, 31)
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="10-K",
                accessed_at=as_of,
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="0000789019",
                source_id="s",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100,
                unit="USD",
                period_start=date(2024, 1, 1),
                period_end=as_of,
            )
        ],
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft Corp 投资研究初稿",
        markdown=(
            "# Microsoft Corp\n\n## 执行摘要\n内容\n## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n## 风险\n内容\n## 数据限制\n内容\n"
            "## 非投资建议\n内容"
        ),
        citation_keys=["fake-claim-1"],
    )

    class _FakeCrew:
        def kickoff(self, inputs=None):
            return SimpleNamespace(tasks_output=[research, analysis, draft])

    recorder = PerformanceRecorder()
    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("perf_artifacts")),
        crew_factory=lambda cfg, rt: _FakeCrew(),
        recorder=recorder,
    )
    runner.run(ResearchRequest(input_company="MSFT", as_of_date=as_of))

    manifest = runner.last_state.run_manifest
    assert manifest["status"] == "published"
    assert "performance" in manifest
    perf = manifest["performance"]
    assert perf["token_usage"] is None
    # 不泄露密钥
    import json

    assert "sk-test-placeholder" not in json.dumps(perf, ensure_ascii=False)
