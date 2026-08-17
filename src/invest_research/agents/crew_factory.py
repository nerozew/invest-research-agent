"""P03-08 组合三个 Task 为 sequential Crew（CrewAI 1.6.1）。

目标（docs/05 P03-08 验收；P05-12B 起支持统一 LLM 接口）：
- 严格顺序：信息搜集 → 财报分析 → 报告撰写（Process.sequential）；
- context 传递：research_task 输出（ResearchPack）→ analysis_task；
  research + analysis 输出 → writer_task（grounded generation：writer 只能读上游两个 pack）；
- 普通测试/CI 传入三角色 FakeLLM（不联网）；生产传入真实 crewai.LLM 或留空
  由 ``build_live_research_crew`` 构造（P05-12B）。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 的 sequential-process/processes 文档）：
- ``Task(..., context=[上游_task])``：上游输出作为下游 context；
- ``Crew(agents=[...], tasks=[...], process=Process.sequential)``；
- ``crew.kickoff()`` 返回 ``CrewOutput``（``result.output`` 为最终 Task 输出）。
"""

from __future__ import annotations

from typing import Any

from crewai import Crew, Process

from invest_research.agents.analysis_task import build_analysis_task
from invest_research.agents.llm_factory import AnyLLM, LLMConfig
from invest_research.agents.pack_parsing import dump_task_output
from invest_research.agents.research_task import build_research_task
from invest_research.agents.writer_task import ArtifactLoader, build_writer_task
from invest_research.settings import ResearchProfile


def _assemble_tasks(
    config: LLMConfig,
    llms: dict[str, AnyLLM],
    tools_by_role: dict[str, list[Any]] | None = None,
    profile: ResearchProfile | None = None,
) -> list[Any]:
    """用三个角色的 LLM 构建 Task，并设置 context 接力（research→analysis→writer）。

    - ``tools_by_role``：可选 per-role CrewAI 工具白名单（如 {"research": [...]}），
      未提供的角色保持原有（Analysis/Writer 内部默认白名单，Research 无工具）；
    - 兼容：不传 ``tools_by_role`` 时与旧行为完全一致（P05-12B 前）。
    """
    tools_by_role = tools_by_role or {}

    research_tools = tools_by_role.get("research")
    # Analysis/Writer 默认白名单在各自 task 模块内部（least-privilege）；
    # 为保持最小改动，这里只允许 research 注入外部工具（其它角色用默认）。
    research_task = build_research_task(
        config, fake=llms["research"], tools=research_tools, profile=profile
    )
    analysis_task = build_analysis_task(config, fake=llms["analysis"], profile=profile)

    # Writer 的 ArtifactReader 运行时读取器：从上游 Task 输出取 pack 真实内容
    # （P06-09 修复：上游 pack 在 sequential 中先执行完成，Writer 开跑前已就绪）。
    # loader 可能被 Crew 内部多方调用，这里确保只捕获一次。
    writer_loader = _task_output_loader(research_task, analysis_task)
    writer_task = build_writer_task(
        config, fake=llms["writer"], profile=profile, artifact_loader=writer_loader
    )

    # context 接力（官方 sequential 模式：上游输出作为下游 context）
    analysis_task.context = [research_task]  # analysis 消费 research 的 ResearchPack
    writer_task.context = [research_task, analysis_task]  # writer 只读两个上游 pack

    return [research_task, analysis_task, writer_task]


def _task_output_loader(*tasks: Any) -> ArtifactLoader:
    """为 Writer 构造运行时工件读取器：给定 artifact_key 返回上游 Task 输出内容。

    约定 artifact_key：
    - ``"research_pack"``  → research_task 输出（ResearchPack）
    - ``"analysis_pack"``  → analysis_task 输出（FinancialAnalysisPack）
    - 其它 key → None（Writer 可用 ArtifactReader 探测，无需发明新 key）。

    实现：CrewAI 1.6.1 sequential 下，上游 Task 执行后 ``task.output``（TaskOutput）
    已填充：``output.pydantic`` 为绑定的 Pydantic 模型（Analysis/Writer），
    ``output.raw`` 为原始 JSON 文本（Research 不绑 pydantic 时的兜底）。
    返回 dict 供 ArtifactReader 直接作为 ``content`` 交给 LLM（依赖方向 agents 内部）。
    """

    def _task_json(task: Any) -> dict[str, Any] | None:
        """P06-09：复用 pack_parsing.dump_task_output 统一读取上游 Task 输出。

        与 flow_wiring._to_packed（parse_pack_output）共用同一读取顺序
        （pydantic → json_dict/exported → raw），保证"解析侧"与"Writer
        ArtifactReader 工具侧"读到的内容一致。
        """
        output = getattr(task, "output", None)
        if output is None:
            # 兼容测试桩：dict 直接作为 Task 的 output（真实路径不走这里）
            direct = getattr(task, "json_dict", None) or getattr(task, "exported_output", None)
            return direct if isinstance(direct, dict) else None
        return dump_task_output(output)

    def loader(artifact_key: str) -> dict[str, Any] | None:
        if artifact_key == "research_pack":
            return _task_json(tasks[0]) if tasks else None
        if artifact_key == "analysis_pack":
            return _task_json(tasks[1]) if len(tasks) > 1 else None
        return None

    return loader


def build_research_crew(
    config: LLMConfig,
    fakes: dict[str, AnyLLM],
    research_tools: list[Any] | None = None,
    profile: ResearchProfile | None = None,
) -> Crew:
    """构建三 Agent 顺序 Crew（统一 LLM 接口）。

    - ``fakes`` 需提供 research / analysis / writer 三个角色的 LLM
      （FakeLLM 不联网；真实 crewai.LLM 走真实推理）；
    - ``research_tools``：可选注入 Research Agent 的工具白名单（P05-12B 生产）；
    - 严格顺序 + context 接力：research → analysis → writer。
    """
    tasks = _assemble_tasks(
        config,
        fakes,
        tools_by_role={"research": research_tools} if research_tools else None,
        profile=profile,
    )
    return Crew(
        agents=[t.agent for t in tasks],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
    )


def build_live_research_crew(
    config: LLMConfig,
    research_tools: list[Any] | None = None,
    profile: ResearchProfile | None = None,
) -> Crew:
    """构建真实三 Agent 顺序 Crew（P05-12B：真实 LLM 生产组装）。

    - 三个角色分别用 ``build_real_llm`` 构造真实 OpenAI-compatible LLM；
    - ``research_tools``：可选注入 Research Agent 的工具白名单（SEC/搜索/下载）；
    - 构造阶段不联网；真正的推理发生在 Crew kickoff 时。
    """
    from invest_research.agents.llm_factory import LLMRole, build_real_llm

    llms = {
        "research": build_real_llm(config, LLMRole.RESEARCH),
        "analysis": build_real_llm(config, LLMRole.ANALYSIS),
        "writer": build_real_llm(config, LLMRole.WRITER),
    }
    return build_research_crew(config, llms, research_tools=research_tools, profile=profile)
