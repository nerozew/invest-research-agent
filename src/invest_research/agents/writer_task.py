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


@tool("ArtifactReader")
def artifact_reader(artifact_key: str) -> dict[str, str]:
    """读取工作流中间工件（ResearchPack / FinancialAnalysisPack 等）。

    确定性实现：这里返回工件读取的契约占位（P04-04 接 DB 后可注入真实存储）。
    当前场景下返回 ``{"artifact_key": ...}`` 表明该工件可被读取。
    """
    # P02-12 的 ArtifactStoreTool 是 P02-01 契约（execute），与 CrewAI BaseTool 不同，
    # 需经 @tool 包装才能挂在 Agent.tools 上；这里先做最小确定性实现，
    # 真实文件读取由 P04 的 API/存储层注入。
    return {"artifact_key": artifact_key, "status": "readable"}


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


# Writer 最小工具白名单（least-privilege：读工件 / 验引用 / 查模板）
_WRITER_TOOLS = [artifact_reader, citation_verifier, template_guide]


def build_writer_agent(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    profile: ResearchProfile | None = None,
) -> Agent:
    """构建报告撰写 Agent（统一 LLM 接口）。

    - 只注入 ArtifactReader + CitationVerifier + TemplateGuide；
    - backstory 使用 writer_prompt_v1（明确禁止引入新事实）；
    - 未传 fake 时用 build_real_llm 构造真实 LLM（P05-12B 删除 NotImplementedError）。
    """
    prompt = load_prompt(PromptName.WRITER)
    llm = fake if fake is not None else build_real_llm(config, LLMRole.WRITER)
    resolved = profile if profile is not None else ResearchProfile.for_mode("deep")
    return Agent(
        role="报告撰写 Agent",
        goal=(
            "只使用上游 ResearchPack 与 FinancialAnalysisPack，产出符合 ReportDraft"
            "契约、带引用的中文报告初稿（不引入新事实）。"
        ),
        backstory=prompt,
        llm=llm,
        tools=_WRITER_TOOLS,
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
) -> Task:
    """构建 Writer Task：fake LLM + 写作工具白名单，输出绑定 ReportDraft。"""
    task_agent = (
        agent
        if agent is not None
        else build_writer_agent(config, fake=fake, profile=profile)
    )
    return Task(
        description=(
            "撰写任务输入：input_company={input_company}，as_of_date={as_of_date}，language={language}。\n"
            "基于上游 research_pack 与 analysis_pack，按 writer_prompt_v1 规则撰写"
            "符合 ReportDraft 契约的中文投资研究初稿：每个事实带 citation key，"
            "区分事实/分析/风险/数据限制，包含非投资建议声明与数据截止日。"
        ),
        expected_output="一个可被 ReportDraft 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        output_pydantic=ReportDraft,
    )


def build_writer_pair(
    config: LLMConfig,
    fake: AnyLLM,
    profile: ResearchProfile | None = None,
) -> tuple[Agent, Task]:
    """返回 (agent, task) 元组（同一 Agent 实例），供 P03-08 组合 Crew。"""
    agent = build_writer_agent(config, fake=fake, profile=profile)
    task = build_writer_task(config, fake=fake, agent=agent, profile=profile)
    return agent, task
