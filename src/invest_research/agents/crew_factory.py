"""P03-08 组合三个 Task 为 sequential Crew（CrewAI 1.6.1，不联网）。

目标（docs/05 P03-08 验收）：
- 严格顺序：信息搜集 → 财报分析 → 报告撰写（Process.sequential）；
- context 传递：research_task 输出（ResearchPack）→ analysis_task；
  research + analysis 输出 → writer_task（grounded generation：writer 只能读上游两个 pack）；
- 完全离线：三个 Agent 都用 FakeLLM，不消耗真实 API。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 的 sequential-process/processes 文档）：
- ``Task(..., context=[上游_task])``：上游输出作为下游 context；
- ``Crew(agents=[...], tasks=[...], process=Process.sequential)``；
- ``crew.kickoff()`` 返回 ``CrewOutput``（``result.output`` 为最终 Task 输出）。

注意：本任务只组合 Crew 并验证 kickoff；真实 LLM 由 P03-08 之后的版本接入。
"""

from __future__ import annotations

from crewai import Crew, Process

from invest_research.agents.analysis_task import build_analysis_task
from invest_research.agents.llm_factory import FakeLLM, LLMConfig
from invest_research.agents.research_task import build_research_task
from invest_research.agents.writer_task import build_writer_task


def build_research_crew(config: LLMConfig, fakes: dict[str, FakeLLM]) -> Crew:
    """构建三 Agent 顺序 Crew。

    - ``fakes`` 需提供 research / analysis / writer 三个角色的 FakeLLM；
    - 严格顺序 + context 接力：research → analysis → writer；
    - writer 的 context 含 research + analysis 两个上游 pack（grounded generation）。
    """
    # 三个 Agent 各自独立（各自携带各自的最小工具白名单）：
    # research=搜索/下载工具，analysis=取数/计算工具，writer=读/引用/模板工具
    research_task = build_research_task(config, fake=fakes["research"])
    analysis_task = build_analysis_task(config, fake=fakes["analysis"])
    writer_task = build_writer_task(config, fake=fakes["writer"])

    # context 接力（官方 sequential 模式：上游输出作为下游 context）
    analysis_task.context = [research_task]  # analysis 消费 research 的 ResearchPack
    writer_task.context = [research_task, analysis_task]  # writer 只读两个上游 pack

    return Crew(
        agents=[research_task.agent, analysis_task.agent, writer_task.agent],
        tasks=[research_task, analysis_task, writer_task],
        process=Process.sequential,
        verbose=False,
    )
