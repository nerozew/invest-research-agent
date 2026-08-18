"""P05.5-2 fast/deep 研究档位测试（ResearchProfile + Agent 预算注入）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from invest_research.agents.analysis_task import build_analysis_agent
from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.agents.research_task import build_research_agent
from invest_research.agents.writer_task import build_writer_agent
from invest_research.settings import ResearchProfile, Settings


def _config() -> LLMConfig:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    return LLMConfig.from_settings(settings)


def _fake(role: LLMRole) -> FakeLLM:
    return FakeLLM(config=_config(), role=role)


def _stop_rpm_controller(agent: object) -> None:
    """停止 CrewAI Agent 的 RPMController 非守护 Timer（防线程泄漏导致进程不退出）。

    CrewAI 1.6.1 在 max_rpm 不为 None 时，由 RPMController 启动
    threading.Timer(60.0, _reset_request_count)（daemon=False，且会循环自重建）。
    测试构造 fast profile Agent 后必须显式停止，否则 Python 会卡在
    threading._shutdown 无法退出。这里安全判断 _rpm_controller 是否存在。
    """
    controller = getattr(agent, "_rpm_controller", None)
    if controller is not None and hasattr(controller, "stop_rpm_counter"):
        controller.stop_rpm_counter()


def test_fast_profile_budgets() -> None:
    p = ResearchProfile.for_mode("fast")
    assert p.mode == "fast"
    assert p.research_max_iter == 8
    assert p.analysis_max_iter == 2
    # P06-11-fix：Writer 需依次读取两个 Pack 再输出 ReportDraft，max_iter=1 会导致
    # 工具参数被当最终输出；fast 档位调整为 5（与 deep 的 5 对齐，仍为最小可用值）。
    assert p.writer_max_iter == 5
    assert p.max_retry_limit == 1
    assert p.max_execution_time == 180
    assert p.max_rpm == 60


def test_deep_profile_budgets() -> None:
    p = ResearchProfile.for_mode("deep")
    assert p.mode == "deep"
    assert p.research_max_iter == 15
    assert p.analysis_max_iter == 10
    assert p.writer_max_iter == 5
    assert p.max_retry_limit == 2
    assert p.max_execution_time == 600
    assert p.max_rpm is None


def test_settings_default_profile_is_deep() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    assert settings.research_profile == "deep"
    assert settings.build_research_profile().mode == "deep"


def test_settings_fast_profile() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
        research_profile="fast",
    )
    assert settings.build_research_profile().research_max_iter == 8


def test_invalid_research_profile_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            llm_api_key="sk-test-placeholder",
            sec_user_agent_contact="test@example.com",
            research_profile="invalid",
        )


def test_research_agent_applies_fast_budget() -> None:
    agent = build_research_agent(
        _config(), fake=_fake(LLMRole.RESEARCH), profile=ResearchProfile.for_mode("fast")
    )
    try:
        assert agent.max_iter == 8
        assert agent.max_retry_limit == 1
        assert agent.max_execution_time == 180
        assert agent.max_rpm == 60
    finally:
        _stop_rpm_controller(agent)


def test_research_agent_defaults_to_deep() -> None:
    agent = build_research_agent(_config(), fake=_fake(LLMRole.RESEARCH))
    try:
        assert agent.max_iter == 15
        assert agent.max_retry_limit == 2
        assert agent.max_execution_time == 600
        assert agent.max_rpm is None
    finally:
        _stop_rpm_controller(agent)


def test_analysis_and_writer_agents_apply_own_max_iter() -> None:
    fast = ResearchProfile.for_mode("fast")
    analysis = build_analysis_agent(_config(), fake=_fake(LLMRole.ANALYSIS), profile=fast)
    writer = build_writer_agent(_config(), fake=_fake(LLMRole.WRITER), profile=fast)
    try:
        assert analysis.max_iter == 2
        # P06-11-fix：fast Writer max_iter 同步为 5（两 Pack 读取 + 最终输出）。
        assert writer.max_iter == 5
        assert analysis.max_retry_limit == 1
        assert writer.max_execution_time == 180
    finally:
        _stop_rpm_controller(analysis)
        _stop_rpm_controller(writer)
