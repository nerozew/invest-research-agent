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
            "研究任务输入：\n"
            "- input_company: {input_company}\n"
            "- as_of_date: {as_of_date}\n"
            "- requested_forms: {requested_forms}\n"
            "- language: {language}\n"
            "- company_identity: {company_identity}\n"
            "- prefetch_summary: {prefetch_summary}\n"
            "要求：\n"
            "1. as_of_date 是唯一允许的数据截止日，必须使用输入中的 {as_of_date}，\n"
            "   禁止自行更换其他日期；\n"
            "2. 优先使用 company_identity 与 prefetch_summary 中已有的结果，\n"
            "   足够时不得重复调用相同工具/参数；\n"
            "3. 在工具白名单内调用信息搜集工具收集来源后，产出符合 ResearchPack\n"
            "   契约的完整结构化来源包；\n"
            "4. Action/Action Input 只是工具调用过程记录，绝不是最终答案；\n"
            "   最终输出必须是可被 ResearchPack 校验的对象。"
        ),
        expected_output="一个可被 ResearchPack 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        # P05.5-fix：不绑 output_pydantic——CrewAI 在 kickoff 内校验失败会直接抛错，
        # 使 runner 的结构化收尾兜底无法生效；改由 runner 解析 + JSON 提取 + 有界收尾。
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
