"""P03-07 构建 Writer Task（CrewAI 1.6.1；P05-12B 起支持统一 LLM 接口）。

目标（docs/05 P03-07 验收）：
- 组装报告撰写 Agent（注入 ``AnyLLM``：FakeLLM 或 crewai.BaseLLM）与 ``Task``；
- Task 绑定 ``output_pydantic=ReportDraft``；
- **只暴露一个聚合读取工具**（least-privilege）：WriterContextReader，
  一次返回两个上游 Pack 与稳定模板章节；Writer 不再用多个确定性工具消耗迭代预算。

CrewAI 1.6.1 关键 API（依据官方 docs/edge 的 AGENTS 模板）：
- ``@tool("Name")`` 装饰器把函数包成 CrewAI 工具；
- ``Agent(..., tools=[tool1, tool2])`` 注入工具白名单。
"""

from __future__ import annotations

from typing import Any, Callable

from crewai import Agent, Task
from crewai.tools import tool

from invest_research.agents.llm_factory import (
    AnyLLM,
    LLMConfig,
    LLMRole,
    StructuredOutputMode,
    build_real_llm,
    structured_output_mode,
)
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


def make_writer_context_reader(
    loader: ArtifactLoader | None = None,
    citation_registry: Any | None = None,
) -> Any:
    """构造 WriterContextReader：一次返回写作所需的完整确定性上下文。

    P06-11B 将两个 ArtifactReader 调用和 TemplateGuide 调用合并为一次读取，
    避免 Writer 在真正输出 ``ReportDraft`` 前耗尽 ``max_iter``。返回值只来自
    已完成的上游 Task 与代码内版本化章节常量，不发起网络请求、不新增事实。

    P06-11F：``citation_registry`` 把合法 citation key（src_<hash>/fr_<hash>）
    的**完整注册表**交给 Writer——模型只能从注册表复制 key，不得自行生成。
    注册表为空（is_empty）时，Writer 必须进入"数据限制"表达，不能伪造引用。
    """
    resolver = loader if loader is not None else _placeholder_loader

    @tool("WriterContextReader")
    def writer_context_reader() -> dict[str, object]:
        """一次读取 ResearchPack、FinancialAnalysisPack、引用注册表与必需章节。"""
        research_pack = resolver("research_pack")
        analysis_pack = resolver("analysis_pack")
        missing: list[str] = []
        if research_pack is None:
            missing.append("research_pack")
        if analysis_pack is None:
            missing.append("analysis_pack")
        registry_payload: dict[str, object] = (
            citation_registry.as_writer_payload()
            if citation_registry is not None
            else {"format": "[src_<hash>] 或 [fr_<hash>]", "entries": []}
        )
        return {
            "status": "ready" if not missing else "partial",
            "research_pack": research_pack,
            "analysis_pack": analysis_pack,
            "citation_registry": registry_payload,
            "required_sections": list(REPORT_SECTIONS),
            "missing": missing,
        }

    return writer_context_reader


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


# CitationVerifier / TemplateGuide 仍作为独立确定性函数供修订流程与单元测试
# 复用；主 Writer 不再逐项调用它们。ReportDraft 生成后的现有质量门禁负责
# 必需章节与 citation_keys 结构检查，修订流程仍可使用 CitationVerifier；
# 不在此虚构“已实现所有引用语义校验”。

# P06-11D：DeepSeek/generic Writer 只输出 Markdown 报告正文（本地确定性组装）。
# 不再要求模型把几千字 Markdown 包装成 JSON——普通自然语言正文由
# ReportDraftAssembler 确定性生成 version/title/citation_keys。
_MARKDOWN_OUTPUT_INSTRUCTION = (
    "\n"
    "输出格式要求：\n"
    "1. 直接输出一份完整的 Markdown 报告正文（章节标题用 ## 或 ###）；\n"
    "2. 不要输出 JSON object，不要使用 ```json/```markdown 代码围栏，"
    "不要把正文包装成任何结构字段；\n"
    "3. 正文中引用上游来源/财务事实时，只能从 WriterContextReader 返回的"
    " citation_registry.entries[].citation_key 复制合法 key，并写成固定格式"
    " [src_<hash>] 或 [fr_<hash>]；禁止自行生成、拼接或猜测任何 key；\n"
    "4. 如果 citation_registry.entries 为空，不要伪造任何 key，"
    "在数据限制章节如实说明没有可用来源或事实；\n"
    "5. 每个来自 SEC、搜索结果或财务事实的关键陈述都必须带上述格式的引用；\n"
    "6. 不得给出买入/卖出建议、持仓比例、目标价或确定性收益承诺；"
    "必须保留非投资建议声明；\n"
    "7. 不要输出任何解释文字、前后缀或自然语言说明——正文本身就是最终答案；\n"
    "8. 工具调用的 Action/Action Input/参数绝不能作为最终答案。"
)


def build_writer_agent(
    config: LLMConfig,
    fake: AnyLLM | None = None,
    profile: ResearchProfile | None = None,
    artifact_loader: ArtifactLoader | None = None,
    citation_registry: Any | None = None,
) -> Agent:
    """构建报告撰写 Agent（统一 LLM 接口）。

    - 只注入 WriterContextReader；一次读取两个 Pack 与模板章节；
    - ``artifact_loader``：可选注入上游工件读取器（生产从 Task 输出读取真实 pack）；
      未注入时 WriterContextReader 用占位实现（向后兼容 fake 测试）；
    - ``citation_registry``（P06-11F）：确定性引用注册表——Writer 只能从注册表
      复制合法 citation key，不得自行生成；为空时进入"数据限制"表达；
    - backstory 使用 writer_prompt_v2（P06-09A：按 completeness 如实组织报告）；
    - 未传 fake 时用 build_real_llm 构造真实 LLM（P05-12B 删除 NotImplementedError）。
    """
    prompt = load_prompt(PromptName.WRITER)
    llm = fake if fake is not None else build_real_llm(config, LLMRole.WRITER)
    resolved = profile if profile is not None else ResearchProfile.for_mode("deep")
    context_reader = make_writer_context_reader(artifact_loader, citation_registry)
    return Agent(
        role="报告撰写 Agent",
        goal=(
            "只使用上游 ResearchPack 与 FinancialAnalysisPack，产出符合 ReportDraft"
            "契约、带引用的中文报告初稿（不引入新事实）。"
        ),
        backstory=prompt,
        llm=llm,
        tools=[context_reader],
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
    citation_registry: Any | None = None,
) -> Task:
    """构建 Writer Task：按供应商结构化输出能力决策输出路径（P06-11B）。

    - qwen（NATIVE_PYDANTIC）：绑定 ``output_pydantic=ReportDraft``，保留原路径；
    - deepseek/generic（JSON_TEXT_LOCAL_VALIDATION）：**不绑定 output_pydantic**
      （也不改用 output_json —— CrewAI 1.6.1 中二者进入同一 ``convert_to_model``），
      Agent 只输出 Markdown 报告正文；Crew 完成后由 ``ReportDraftAssembler``
      本地确定性组装为 ``ReportDraft``（P06-11D）；
    - ``citation_registry``（P06-11F）：确定性引用注册表，透传给 WriterContextReader。
    """
    task_agent = (
        agent
        if agent is not None
        else build_writer_agent(
            config,
            fake=fake,
            profile=profile,
            artifact_loader=artifact_loader,
            citation_registry=citation_registry,
        )
    )
    description = (
        "撰写任务输入：input_company={input_company}，as_of_date={as_of_date}，language={language}。\n"
        "按 writer_prompt_v2 规则撰写符合 ReportDraft 契约的中文投资研究初稿。\n"
        "写作开始时只调用一次 WriterContextReader，读取 research_pack、analysis_pack 与"
        " required_sections；不要重复调用工具。若 status=partial，必须把 missing 中的"
        "缺失项写入数据限制章节，不得编造。读取后立即输出最终 ReportDraft。\n"
        "必须读取 analysis_pack.completeness 并按状态组织报告：complete 正常撰写财务表现；"
        "partial 把 limitations 中的缺失数据及原因写入数据限制章节；unavailable 在财务/"
        "指标章节仅说明数据不可用（引用 unavailable_reason），不得推断或编造财务数据。\n"
        "要求：每个事实带 citation key，区分事实/分析/风险/数据限制，"
        "包含非投资建议声明与数据截止日。"
    )
    if (
        structured_output_mode(config) == StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION
    ):
        # P06-11D：deepseek/generic 只输出 Markdown 报告正文，不触发 CrewAI
        # 远程 Pydantic parse；由本地 ReportDraftAssembler 确定性组装为 ReportDraft。
        description = description + _MARKDOWN_OUTPUT_INSTRUCTION
        return Task(
            description=description,
            expected_output=(
                "一份完整的 Markdown 报告正文（非 JSON、非自由文本说明）。"
                "由本地 ReportDraftAssembler 组装为 ReportDraft。"
            ),
            agent=task_agent,
            # 不绑定 output_pydantic：避免 CrewAI 在输出转换阶段调用
            # beta.chat.completions.parse(response_model=...)（DeepSeek HTTP 400）。
        )
    return Task(
        description=description,
        expected_output="一个可被 ReportDraft 校验通过的结构化对象（非自由文本）。",
        agent=task_agent,
        output_pydantic=ReportDraft,
    )


def build_writer_pair(
    config: LLMConfig,
    fake: AnyLLM,
    profile: ResearchProfile | None = None,
    artifact_loader: ArtifactLoader | None = None,
    citation_registry: Any | None = None,
) -> tuple[Agent, Task]:
    """返回 (agent, task) 元组（同一 Agent 实例），供 P03-08 组合 Crew。"""
    agent = build_writer_agent(
        config,
        fake=fake,
        profile=profile,
        artifact_loader=artifact_loader,
        citation_registry=citation_registry,
    )
    task = build_writer_task(
        config,
        fake=fake,
        agent=agent,
        profile=profile,
        artifact_loader=artifact_loader,
        citation_registry=citation_registry,
    )
    return agent, task
