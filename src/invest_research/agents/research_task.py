"""P03-05 用 fake LLM 构建 Research Task（CrewAI 1.6.1，不联网）。

目标（docs/05 P03-05 验收）：
- 组装信息搜集 Agent（注入 ``FakeLLM``）与 ``Task``；
- Task 绑定 ``output_pydantic=ResearchPack``：输出必须能被解析为结构化 pack；
- 完全离线：fake LLM 按预置响应返回，不消耗真实 API。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 文档）：
- ``Agent(role=, goal=, backstory=, llm=, allow_delegation=False)``；
- ``Task(description=, expected_output=, agent=, output_pydantic=<类，非实例>)``。

注意：P03-05 只构建 Agent+Task，不组合 Crew（P03-08 才做）。
"""

from __future__ import annotations

from crewai import Agent, Task

from invest_research.agents.llm_factory import FakeLLM, LLMConfig
from invest_research.domain.models import ResearchPack
from invest_research.prompts.loader import PromptName, load_prompt


def build_research_agent(config: LLMConfig, fake: FakeLLM | None = None) -> Agent:
    """构建信息搜集 Agent。

    - 正常测试传 ``fake``（FakeLLM 实例，不联网）；
    - 生产时 future 可传真实 LLM（`_build_real_llm`，P03-08 实现）。
    """
    prompt = load_prompt(PromptName.RESEARCH)
    llm = fake if fake is not None else None  # P03-08 前仅支持 fake 测试
    if llm is None:  # pragma: no cover - 真实 LLM 尚未接入
        raise NotImplementedError("P03-05 仅支持 fake LLM；真实 LLM 待 P03-08 接入")
    return Agent(
        role="信息搜集 Agent",
        goal=(
            "围绕给定 CompanyIdentity 与 as_of_date，系统性收集、去重并整理"
            "公开来源，形成 ResearchPack。"
        ),
        backstory=prompt,
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )


def build_research_task(
    config: LLMConfig,
    fake: FakeLLM | None = None,
    agent: Agent | None = None,
) -> Task:
    """构建 Research Task：用 fake LLM，输出绑定 ResearchPack。

    - ``agent`` 预置时复用该实例（保证 Task 与返回的 Agent 是同一个对象）；
    - 默认内部构建一个新 Agent。
    """
    task_agent = agent if agent is not None else build_research_agent(config, fake=fake)
    return Task(
        description=(
            "基于输入的公司身份与 as_of_date，调用信息搜集工具的职责范围内工具，"
            "产出符合 ResearchPack 契约的结构化来源包。"
        ),
        expected_output="一个可被 ResearchPack 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        output_pydantic=ResearchPack,
    )


def build_research_pair(config: LLMConfig, fake: FakeLLM) -> tuple[Agent, Task]:
    """返回 (agent, task) 元组（同一 Agent 实例），供 P03-08 组合 Crew 直接使用。"""
    agent = build_research_agent(config, fake=fake)
    task = build_research_task(config, fake=fake, agent=agent)
    return agent, task
