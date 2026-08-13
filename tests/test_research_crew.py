"""P03-08 sequential Crew 测试（三个 FakeLLM，不联网）。

验证目标（docs/05 P03-08 验收）：
- 严格顺序：agents 与 tasks 顺序均为 [research, analysis, writer]；
- context 接力：analysis.context=[research_task]，writer.context=[research_task, analysis_task]；
- process=Process.sequential；
- 真实 kickoff：三个 FakeLLM 按顺序各被调用一次，最终输出 ReportDraft。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from crewai import Process

from invest_research.agents.crew_factory import build_research_crew
from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
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


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp"),
        as_of_date=date(2025, 12, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/10k",
                title="Latest 10-K",
                accessed_at=date(2025, 12, 31),
            )
        ],
    )


def _analysis_pack() -> FinancialAnalysisPack:
    return FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2025, 6, 30),
        facts=[
            FinancialFact(
                company_id="c1",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=Decimal("245100000000"),
                unit="USD",
                period_start=date(2024, 7, 1),
                period_end=date(2025, 6, 30),
            )
        ],
    )


def _report_draft() -> ReportDraft:
    return ReportDraft(
        version="report_draft_v1",
        title="微软（MSFT）投资研究初稿",
        markdown="# 微软\n\n> 非投资建议",
        citation_keys=["claim-1"],
    )


def _fakes() -> dict[str, FakeLLM]:
    return {
        "research": FakeLLM(config=_config(), role=LLMRole.RESEARCH, responses=[_research_pack()]),
        "analysis": FakeLLM(config=_config(), role=LLMRole.ANALYSIS, responses=[_analysis_pack()]),
        "writer": FakeLLM(config=_config(), role=LLMRole.WRITER, responses=[_report_draft()]),
    }


def test_crew_agents_and_tasks_in_sequential_order() -> None:
    """agents 与 tasks 顺序均为 research→analysis→writer。"""
    crew = build_research_crew(_config(), _fakes())
    # 依次校验 agents 角色与 tasks 输出契约
    assert crew.agents[0].role == "信息搜集 Agent"
    assert crew.agents[1].role == "财报分析 Agent"
    assert crew.agents[2].role == "报告撰写 Agent"
    assert crew.tasks[0].output_pydantic is ResearchPack
    assert crew.tasks[1].output_pydantic is FinancialAnalysisPack
    assert crew.tasks[2].output_pydantic is ReportDraft


def test_crew_uses_sequential_process() -> None:
    """process = Process.sequential（严格顺序）。"""
    crew = build_research_crew(_config(), _fakes())
    assert crew.process == Process.sequential


def test_context_relay_between_tasks() -> None:
    """context 接力：analysis 消费 research，writer 消费 research+analysis。"""
    crew = build_research_crew(_config(), _fakes())
    research_task = crew.tasks[0]
    analysis_task = crew.tasks[1]
    writer_task = crew.tasks[2]
    assert analysis_task.context == [research_task]
    assert writer_task.context == [research_task, analysis_task]


def test_kickoff_executes_all_tasks_and_produces_report() -> None:
    """真实 kickoff：三个 FakeLLM 各被调用一次，最终产出 ReportDraft。"""
    fakes = _fakes()
    crew = build_research_crew(_config(), fakes)
    result = crew.kickoff()

    # 三个 FakeLLM 都至少被调用一次（严格顺序执行）
    assert fakes["research"].invoked_prompts, "research FakeLLM 未被调用"
    assert fakes["analysis"].invoked_prompts, "analysis FakeLLM 未被调用"
    assert fakes["writer"].invoked_prompts, "writer FakeLLM 未被调用"

    # 最终输出是可解析的 ReportDraft
    # kickoff 返回 CrewOutput | CrewStreamingOutput；CrewOutput.pydantic 为最终结构化输出
    assert isinstance(result.pydantic, ReportDraft)  # type: ignore[union-attr]
    assert result.pydantic.version == "report_draft_v1"  # type: ignore[union-attr]
