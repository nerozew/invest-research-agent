"""P03-05 Research Task 测试（fake LLM，不联网）。

验证目标（docs/05 P03-05 验收）：
- Agent 可从 P03-01 FakeLLM 注入（BaseLLM 子类）；
- Task 绑定 output_pydantic=ResearchPack（输出必须能解析为结构化 pack）；
- fake LLM 的 call()（带 response_model）能把预置响应实例化为 ResearchPack；
- 整个过程不联网、不消耗真实 API 费用。

CrewAI 1.6.1 API 依据官方文档：Agent(role/goal/backstory/llm)、
Task(description/expected_output/agent/output_pydantic)。
"""

from __future__ import annotations

from datetime import date

import pytest
from crewai import BaseLLM

from invest_research.agents.llm_factory import (
    FakeLLM,
    LLMConfig,
    LLMRole,
    OpenAICompatibleLLMFactory,
)
from invest_research.agents.research_task import (
    build_research_agent,
    build_research_pair,
    build_research_task,
)
from invest_research.domain.models import (
    CompanyIdentity,
    ResearchPack,
    Source,
    SourceType,
)
from invest_research.settings import Settings


def _config() -> LLMConfig:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    return LLMConfig.from_settings(settings)


def _sample_research_pack() -> ResearchPack:
    """构造合法的 ResearchPack 样本（用于 fake 预置响应）。"""
    return ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        as_of_date=date(2025, 12, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="Latest 10-K",
                accessed_at=date(2025, 12, 31),
            )
        ],
    )


def test_fake_llm_is_base_llm_subclass() -> None:
    """P03-01 FakeLLM 可被 CrewAI 接受（继承 BaseLLM）。"""
    factory = OpenAICompatibleLLMFactory()
    fake = factory.create_fake(_config(), LLMRole.RESEARCH)
    # BaseLLM 要求 model 属性（由超类构造）
    assert fake.model
    assert fake.model_name == "qwen-max"
    assert isinstance(fake, BaseLLM)


def test_build_research_agent_with_fake() -> None:
    """用 fake LLM 能构建信息搜集 Agent，且不联网。"""
    fake = FakeLLM(config=_config(), role=LLMRole.RESEARCH)
    agent = build_research_agent(_config(), fake=fake)

    assert agent.role == "信息搜集 Agent"
    assert agent.llm is not None


def test_build_research_task_binds_output_pydantic() -> None:
    """Task 绑定 output_pydantic=ResearchPack（类而非实例）。"""
    fake = FakeLLM(config=_config(), role=LLMRole.RESEARCH)
    task = build_research_task(_config(), fake=fake)

    # output_pydantic 是 ResearchPack 类（CrewAI 期望类，不是实例）
    assert task.output_pydantic is ResearchPack


def test_build_research_pair_returns_agent_and_task() -> None:
    """build_research_pair 返回 (agent, task)，供 P03-08 组合 Crew。"""
    fake = FakeLLM(config=_config(), role=LLMRole.RESEARCH)
    agent, task = build_research_pair(_config(), fake=fake)

    assert agent.role == "信息搜集 Agent"
    assert task.output_pydantic is ResearchPack
    # agent 与 task 内 agent 一致
    assert task.agent == agent


def test_fake_llm_call_instantiates_research_pack() -> None:
    """fake LLM 的 call(response_model=ResearchPack) 把预置响应实例化为 pack。"""
    sample = _sample_research_pack()
    fake = FakeLLM(config=_config(), role=LLMRole.RESEARCH, responses=[sample])

    # response_model 传入时，CrewAI 期望返回 BaseModel 实例
    result = fake.call(
        messages="请收集 MSFT 的公开信息",
        response_model=ResearchPack,
    )

    assert isinstance(result, ResearchPack)
    assert result.version == "research_pack_v1"
    assert result.company_identity.cik == "0000789019"
    # fake 记录了被调用的 prompt
    assert fake.invoked_prompts == ["请收集 MSFT 的公开信息"]


def test_build_research_agent_requires_fake() -> None:
    """P03-05 暂不支持真实 LLM：未传 fake 时抛 NotImplementedError（不联网）。"""
    with pytest.raises(NotImplementedError):
        build_research_agent(_config(), fake=None)
