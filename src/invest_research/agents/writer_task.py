"""P03-07 构建 Writer Task（CrewAI 1.6.1；P05-12B 起支持统一 LLM 接口）。

目标（docs/05 P03-07 验收）：
- 组装报告撰写 Agent（注入 ``AnyLLM``：FakeLLM 或 crewai.BaseLLM）与 ``Task``；
- Task 绑定 ``output_pydantic=ReportDraft``；
- **只暴露允许的写作工具**（least-privilege）：ArtifactReader + CitationVerifier + TemplateGuide，
  不暴露搜索/计算等无关工具；Writer 只能使用上游 context（grounded generation）。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 的 AGENTS 模板）：
- ``@tool("Name")`` 装饰器把函数包成 CrewAI 工具；
- ``Agent(..., tools=[tool1, tool2])`` 注入工具白名单。
"""

from __future__ import annotations

from typing import Any, Callable

from crewai import Agent, Task
from crewai.tools import tool

from invest_research.agents.llm_factory import AnyLLM, LLMConfig, LLMRole, build_real_llm
from invest_research.domain.models import ReportDraft
from invest_research.prompts.loader import PromptName, load_prompt
from invest_research.settings import ResearchProfile
from invest_research.tools.citation_verifier import (
    CitationCheckResult,
    CitationSourceRef,
    verify_claim,
)

# PRD §7 报告标准章节（TemplateGuide 提供的模板结构）
REPORT_SECTIONS: tuple[str, ...] = (
    "封面信息",
    "执行摘要",
    "公司与业务概览",
    "近期重要事件与行业背景",
    "财务表现",
    "关键指标表",
    "风险因素与催化因素",
    "数据限制",
    "来源清单与非投资建议声明",
)

# ArtifactReader 的可注入读取器：artifact_key → 可序列化内容 dict。
# Agent 通过该回调在运行时读取上游 pack 的真实内容（依赖方向 infrastructure→agents）。
ArtifactLoader = Callable[[str], dict[str, Any] | None]


def _placeholder_loader(artifact_key: str) -> dict[str, Any]:
    """默认占位读取器：返回契约占位（未注入真实存储时保持向后兼容）。"""
    return {"artifact_key": artifact_key, "status": "readable"}


def make_artifact_reader(loader: ArtifactLoader | None = None) -> Any:
    """构造 ArtifactReader CrewAI 工具。

    - ``loader`` 为 None 时使用占位实现（fake 测试/无真实存储）；
    - 生产路径注入可回调闭包，运行时读取上游 Task 真实输出。
    """
    resolver = loader if loader is not None else _placeholder_loader

    @tool("ArtifactReader")
    def artifact_reader(artifact_key: str) -> dict[str, object]:
        """读取工作流中间工件（ResearchPack / FinancialAnalysisPack 等），返回其真实内容。"""
        content = resolver(artifact_key)
        if content is None:
            return {"artifact_key": artifact_key, "status": "not_found"}
        return {"artifact_key": artifact_key, "status": "readable", "content": content}

    return artifact_reader


@tool("CitationVerifier")
def citation_verifier(
    claim: str, key_numbers: list[str], url: str | None = None
) -> dict[str, object]:
    """验证一条报告 claim 是否可被来源与关键数字支撑（确定性，不靠 LLM 猜）。

    - 走 P02-19 的纯函数 ``verify_claim``；
    - 从 URL 构造最小 CitationSourceRef；返回 valid + failures。
    """
    # claim 中没有数字或 source 时 verify_claim 会给出 failure
    # （MISSING_SOURCE / NUMBER_UNSUPPORTED）
    source: CitationSourceRef | None = None
    if url:
        source = CitationSourceRef(url=url)
    valid, failures = verify_claim(
        claim=claim, key_numbers=key_numbers, source=source, locator=None
    )
    result: CitationCheckResult = CitationCheckResult(
        valid=valid,
        failures=list(failures),
    )
    return result.model_dump()


@tool("TemplateGuide")
def template_guide(section_name: str | None = None) -> dict[str, object]:
    """查阅报告模板（PRD §7 标准章节）。

    - ``section_name`` 为空时返回全部章节列表；
    - 否则返回该章节是否在模板内（避免 Writer 发明新章节）。
    """
    if section_name is None:
        return {"sections": list(REPORT_SECTIONS)}
    return {"is_known": section_name in REPORT_SECTIONS, "section": section_name}


# Writer 最小工具白名单（least-privilege：读工件 / 验引用 / 查模板；
# ArtifactReader 可注入真实 loader）。
_WRITER_TOOLS = [make_artifact_reader(), citation_verifier, template_guide]


def build_writer_agent(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    profile: ResearchProfile | None = None,
    artifact_loader: ArtifactLoader | None = None,
) -> Agent:
    """构建报告撰写 Agent（统一 LLM 接口）。

    - 只注入 ArtifactReader + CitationVerifier + TemplateGuide；
    - ``artifact_loader``：可选注入上游工件读取器（生产从 Task 输出读取真实 pack）；
      未注入时 ArtifactReader 用占位实现（向后兼容 fake 测试）；
    - backstory 使用 writer_prompt_v1（明确禁止引入新事实）；
    - 未传 fake 时用 build_real_llm 构造真实 LLM（P05-12B 删除 NotImplementedError）。
    """
    prompt = load_prompt(PromptName.WRITER)
    llm = fake if fake is not None else build_real_llm(config, LLMRole.WRITER)
    resolved = profile if profile is not None else ResearchProfile.for_mode("deep")
    reader = make_artifact_reader(artifact_loader)
    return Agent(
        role="报告撰写 Agent",
        goal=(
            "只使用上游 ResearchPack 与 FinancialAnalysisPack，产出符合 ReportDraft"
            "契约、带引用的中文报告初稿（不引入新事实）。"
        ),
        backstory=prompt,
        llm=llm,
        tools=[reader, citation_verifier, template_guide],
        allow_delegation=False,
        verbose=False,
        max_iter=resolved.writer_max_iter,
        max_retry_limit=resolved.max_retry_limit,
        max_execution_time=resolved.max_execution_time,
        max_rpm=resolved.max_rpm,
    )


def build_writer_task(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    agent: Agent | None = None,
    profile: ResearchProfile | None = None,
    artifact_loader: ArtifactLoader | None = None,
) -> Task:
    """构建 Writer Task：fake LLM + 写作工具白名单，输出绑定 ReportDraft。"""
    task_agent = (
        agent
        if agent is not None
        else build_writer_agent(
            config, fake=fake, profile=profile, artifact_loader=artifact_loader
        )
    )
    return Task(
        description=(
            "撰写任务输入：input_company={input_company}，as_of_date={as_of_date}，language={language}。\n"
            "按 writer_prompt_v1 规则撰写符合 ReportDraft 契约的中文投资研究初稿。\n"
            "写作素材通过 ArtifactReader 工具读取：调用 ArtifactReader(\"research_pack\") 与 "
            "ArtifactReader(\"analysis_pack\") 获取上游真实内容（若未读到内容，必须明确写入"
            "数据限制章节，不得编造）。\n"
            "要求：每个事实带 citation key，区分事实/分析/风险/数据限制，"
            "包含非投资建议声明与数据截止日。"
        ),
        expected_output="一个可被 ReportDraft 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        output_pydantic=ReportDraft,
    )


def build_writer_pair(
    config: LLMConfig,
    fake: AnyLLM,
    profile: ResearchProfile | None = None,
    artifact_loader: ArtifactLoader | None = None,
) -> tuple[Agent, Task]:
    """返回 (agent, task) 元组（同一 Agent 实例），供 P03-08 组合 Crew。"""
    agent = build_writer_agent(
        config, fake=fake, profile=profile, artifact_loader=artifact_loader
    )
    task = build_writer_task(
        config, fake=fake, agent=agent, profile=profile, artifact_loader=artifact_loader
    )
    return agent, task
