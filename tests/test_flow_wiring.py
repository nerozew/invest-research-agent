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


def test_live_runner_run_not_allowed_yet() -> None:
    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner, LiveResearchFlowRunner)

    with pytest.raises(NotImplementedError):
        runner.run(_request())
