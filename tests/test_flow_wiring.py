"""P05-12A FLOW_MODE=fake/live 生产 Flow wiring contract tests（离线，不联网）。

验收（对齐路线图 P05-12A）：
- fake/live 模式切换正确；普通测试/CI 默认 fake，不产生模型费用；
- live 缺 LLM_API_KEY 时 fail-fast（可读错误）；
- live 只做 wiring 契约验证（注入 fake LLM 组装 Crew），不触发真实模型调用。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.flow_wiring import (
    FlowModeError,
    LiveResearchFlowRunner,
    build_flow_runner,
)
from invest_research.infrastructure.queue.flow_adapter import ResearchFlowRunner
from invest_research.settings import Settings


def _settings(*, flow_mode: str = "fake", api_key: str = "sk-test") -> Settings:
    """构造不读 .env 的 Settings（必需字段 + flow_mode/llm_api_key 覆盖）。"""
    return Settings(
        _env_file=None,
        llm_api_key=api_key,
        sec_user_agent_contact="test@example.com",
        flow_mode=flow_mode,
    )


def _config() -> LLMConfig:
    return LLMConfig.from_settings(_settings())


def _fakes() -> dict[str, FakeLLM]:
    """构造三个 fake LLM（research/analysis/writer），不联网。"""
    config = _config()
    return {
        "research": FakeLLM(config=config, role=LLMRole.RESEARCH),
        "analysis": FakeLLM(config=config, role=LLMRole.ANALYSIS),
        "writer": FakeLLM(config=config, role=LLMRole.WRITER),
    }


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))


# ---- 1. 默认 fake 模式（普通测试/CI 不产生费用）----


def test_default_flow_mode_is_fake() -> None:
    assert _settings().flow_mode == "fake"
    assert _settings(flow_mode="live").flow_mode == "live"


def test_invalid_flow_mode_rejected_by_pydantic() -> None:
    with pytest.raises(ValidationError):
        _settings(flow_mode="invalid")


# ---- 2. fake 分支：返回 ResearchFlowRunner 并可离线跑全链 ----


def test_build_flow_runner_fake_returns_offline_runner() -> None:
    runner = build_flow_runner(_settings(flow_mode="fake"))
    assert isinstance(runner, ResearchFlowRunner)


def test_fake_runner_runs_offline_full_chain() -> None:
    runner = build_flow_runner(_settings(flow_mode="fake"))
    runner.run(_request())
    assert runner.last_state is not None
    assert runner.last_state.quality_report is not None
    assert runner.last_state.run_manifest is not None


# ---- 3. live 分支：缺 key fail-fast；带 key 返回 live runner ----


def test_live_mode_missing_key_fails_fast() -> None:
    with pytest.raises(FlowModeError) as exc_info:
        build_flow_runner(_settings(flow_mode="live", api_key=""))
    assert "LLM_API_KEY" in str(exc_info.value)


def test_live_mode_whitespace_key_fails_fast() -> None:
    with pytest.raises(FlowModeError):
        build_flow_runner(_settings(flow_mode="live", api_key="   "))


def test_live_mode_with_key_returns_live_runner() -> None:
    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner, LiveResearchFlowRunner)


def test_live_runner_config_does_not_leak_key() -> None:
    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-super-secret"))
    assert isinstance(runner, LiveResearchFlowRunner)
    assert "sk-super-secret" not in str(runner.config)
    assert "sk-super-secret" not in repr(runner.config)


# ---- 4. live wiring 契约：Crew 组装不联网，run 暂不允许 ----


def test_live_runner_assembles_crew_contract() -> None:
    from crewai.crew import Crew

    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner, LiveResearchFlowRunner)

    crew = runner.assemble_crew(_fakes())
    assert isinstance(crew, Crew)
    assert len(crew.agents) == 3
    assert len(crew.tasks) == 3


def test_live_runner_run_with_injected_fake_crew_offline(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """run() 可通过注入 fake crew 离线跑完整控制流（不联网，不调真实模型）。"""
    from types import SimpleNamespace

    from invest_research.domain.models import (
        CompanyIdentity,
        FinancialAnalysisPack,
        FinancialFact,
        ReportDraft,
        ResearchPack,
        Source,
        SourceType,
    )

    as_of = _request().as_of_date
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="Latest 10-K",
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
                source_id="fake-source",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100000000000,
                unit="USD",
                period_start=date(2024, 1, 1),
                period_end=as_of,
            )
        ],
        analysis_notes="fake 分析占位",
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft Corp 投资研究初稿",
        markdown=(
            "# Microsoft Corp\n\n"
            "## 执行摘要\n内容\n"
            "## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n"
            "## 风险\n内容\n"
            "## 数据限制\n内容\n"
            "## 非投资建议\n内容"
        ),
        citation_keys=["fake-claim-1"],
    )

    class _FakeCrew:
        def kickoff(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(tasks_output=[research, analysis, draft])

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("live_artifacts")),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    runner.run(_request())

    assert runner.last_state is not None
    assert runner.last_state.research_pack is not None
    assert runner.last_state.analysis_pack is not None
    assert runner.last_state.report_draft is not None
    assert runner.last_state.quality_report is not None
    assert runner.last_state.run_manifest.get("status") in ("published", "rejected")


def test_live_failure_does_not_degrade_to_fake(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """live 失败抛 LiveFlowExecutionError，绝不偷偷调用 fake。"""
    from invest_research.infrastructure.flow_wiring import LiveFlowExecutionError

    class _BrokenCrew:
        def kickoff(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("live_broken")),
        crew_factory=lambda cfg, rt: _BrokenCrew(),  # type: ignore[no-any-return]
    )
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    assert runner.last_state is None  # 不残留运行状态
