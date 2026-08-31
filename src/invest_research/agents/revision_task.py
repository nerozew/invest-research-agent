"""P03-18 Writer 定向修订 Task（Phase 3.5；P05-12B 起支持统一 LLM 接口）。

对齐 docs/04 §2.5 与 docs/06 §12.4.1（writer_revision_prompt_v1）：
- 只修复 RevisionRequest.issues 指出的问题；
- 不新增上游不存在的事实；不修改 FinancialFact/MetricResult；
- 保留仍有效的引用；缺少证据时明确"无法修订"。

实现：版本化修订提示词 + 复用 Writer 最小工具白名单
（ArtifactReader/CitationVerifier/TemplateGuide，与 P03-07 一致），
Task 输出绑定 ReportDraft，description 注入 QualityIssue 列表与修订序号。
"""

from __future__ import annotations

from crewai import Agent, Task

from invest_research.agents.llm_factory import AnyLLM, LLMConfig, LLMRole, build_real_llm
from invest_research.agents.writer_task import (
    citation_verifier,
    make_artifact_reader,
    template_guide,
)
from invest_research.domain.models import ReportDraft
from invest_research.domain.quality import QualityIssue

# 版本化定向修订提示词（对应 docs/06 §12.4.1 writer_revision_prompt_v1）
REVISION_PROMPT_V1 = (
    "你是负责定向修订的研究报告编辑。只修复给出的 QualityIssue 指出的问题。"
    "只能使用原始 research_pack 与 analysis_pack 与本次 RevisionRequest；"
    "不得新增上游不存在的事实或数字；不得修改 FinancialFact 与 MetricResult 的任何值；"
    "必须保留已有且仍有效的引用键；证据不足时不猜测，删除无法支撑的结论或标注'证据不足'；"
    "若无法完成定向修订，明确返回'无法修订'。禁止引入买卖建议/目标价，禁止删除'非投资建议'声明。"
)

# 修订 Agent 白名单：读旧稿 + 验引用 + 查模板（不给新增来源/计算能力）
_REVISION_TOOLS = [make_artifact_reader(), citation_verifier, template_guide]


def build_revision_agent(config: LLMConfig, fake: AnyLLM | None = None) -> Agent:
    """定向修订 Agent：复用 Writer 最小工具白名单 + 修订提示词。

    - 传 fake 时直接复用（不联网）；未传时构造真实 LLM（P05-12B）。
    """
    llm = fake if fake is not None else build_real_llm(config, LLMRole.WRITER)
    return Agent(
        role="定向修订编辑",
        goal="只修复 QualityIssue 指出的问题，产出修订版 ReportDraft（不新增事实）。",
        backstory=REVISION_PROMPT_V1,
        llm=llm,
        tools=_REVISION_TOOLS,
        allow_delegation=False,
        verbose=False,
    )


def build_revision_task(
    config: LLMConfig,
    fake: AnyLLM,
    issues: list[QualityIssue],
    original_draft_version: str,
    revision_number: int,
    agent: Agent | None = None,
) -> Task:
    """构建定向修订 Task（统一 LLM 接口）。"""
    if not issues:
        raise ValueError("issues 不能为空：定向修订必须指定要修复的问题")
    if revision_number < 1:
        raise ValueError("revision_number 必须 ≥ 1")
    task_agent = agent if agent is not None else build_revision_agent(config, fake=fake)
    issue_block = "\n".join(f"- [{i.code}] {i.message}" for i in issues)
    description = (
        f"请对报告草稿版本 '{original_draft_version}' 做第 {revision_number} 次定向修订。\n"
        f"需要修复的问题：\n{issue_block}\n"
        "只修改以上问题；不新增事实、不改数字、保留有效引用；"
        "输出符合 ReportDraft 契约的修订版草稿。"
    )
    return Task(
        description=description,
        expected_output="返回修订版 ReportDraft 结构化对象（如无法修订，返回'无法修订'情况说明）。",
        agent=task_agent,
        output_pydantic=ReportDraft,
    )


def build_revision_pair(
    config: LLMConfig,
    fake: AnyLLM,
    issues: list[QualityIssue],
    original_draft_version: str,
    revision_number: int,
) -> tuple[Agent, Task]:
    """返回 (agent, task)，同一实例供 Flow 复用。"""
    agent = build_revision_agent(config, fake=fake)
    task = build_revision_task(
        config, fake, issues, original_draft_version, revision_number, agent=agent
    )
    return agent, task
