"""P03-05 构建 Research Task（CrewAI 1.6.1；P05-12B 起支持统一 LLM 接口）。

目标（docs/05 P03-05 验收）：
- 组装信息搜集 Agent（注入 ``AnyLLM``：FakeLLM 或 crewai.BaseLLM）与 ``Task``；
- Task 绑定 ``output_pydantic=ResearchPack``：输出必须能被解析为结构化 pack；
- 普通测试/CI 传 ``fake``（FakeLLM，不联网）；生产传真实 LLM 或留空
  （P05-12B：未传 fake 时用 ``build_real_llm`` 构造真实 OpenAI-compatible LLM）。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 文档）：
- ``Agent(role=, goal=, backstory=, llm=, allow_delegation=False)``；
- ``Task(description=, expected_output=, agent=, output_pydantic=<类，非实例>)``。
"""

from __future__ import annotations

from typing import Any

from crewai import Agent, Task

from invest_research.agents.llm_factory import AnyLLM, LLMConfig, LLMRole, build_real_llm
from invest_research.domain.models import ResearchPack
from invest_research.prompts.loader import PromptName, load_prompt
from invest_research.settings import ResearchProfile


def build_research_agent(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    tools: list[Any] | None = None,
    profile: ResearchProfile | None = None,
) -> Agent:
    """构建信息搜集 Agent（统一 LLM 接口）。

    - 传 ``fake``（FakeLLM 或其它 BaseLLM）时直接复用，不联网；
    - 未传时用 ``build_real_llm`` 构造真实 OpenAI-compatible LLM
      （P05-12B：删除"仅支持 fake"的 NotImplementedError；构造阶段不联网）；
    - ``tools``：可选 CrewAI 工具白名单（P05-12B 生产注入真实 SEC/搜索/下载工具；
      默认 None 保持无工具行为，兼容现有测试）。
    """
    prompt = load_prompt(PromptName.RESEARCH)
    llm = fake if fake is not None else build_real_llm(config, LLMRole.RESEARCH)
    resolved = profile if profile is not None else ResearchProfile.for_mode("deep")
    return Agent(
        role="信息搜集 Agent",
        goal=(
            "围绕给定 CompanyIdentity 与 as_of_date，系统性收集、去重并整理"
            "公开来源，形成 ResearchPack。"
        ),
        backstory=prompt,
        llm=llm,
        tools=tools or [],
        allow_delegation=False,
        verbose=False,
        max_iter=resolved.research_max_iter,
        max_retry_limit=resolved.max_retry_limit,
        max_execution_time=resolved.max_execution_time,
        max_rpm=resolved.max_rpm,
    )


def build_research_task(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    agent: Agent | None = None,
    tools: list[Any] | None = None,
    profile: ResearchProfile | None = None,
) -> Task:
    """构建 Research Task：统一 LLM 接口，输出绑定 ResearchPack。

    - ``agent`` 预置时复用该实例（保证 Task 与返回的 Agent 是同一个对象）；
    - 默认内部构建一个新 Agent（可传 ``tools`` 注入生产工具白名单）。
    """
    task_agent = (
        agent
        if agent is not None
        else build_research_agent(config, fake=fake, tools=tools, profile=profile)
    )
    return Task(
        description=(
            "基于输入的公司身份与 as_of_date，调用信息搜集工具的职责范围内工具，"
            "产出符合 ResearchPack 契约的结构化来源包。"
        ),
        expected_output="一个可被 ResearchPack 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        output_pydantic=ResearchPack,
    )


def build_research_pair(
    config: LLMConfig,
    fake: AnyLLM,
    tools: list[Any] | None = None,
    profile: ResearchProfile | None = None,
) -> tuple[Agent, Task]:
    """返回 (agent, task) 元组（同一 Agent 实例），供组合 Crew 直接使用。"""
    agent = build_research_agent(config, fake=fake, tools=tools, profile=profile)
    task = build_research_task(config, fake=fake, agent=agent, tools=tools, profile=profile)
    return agent, task
