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
from types import SimpleNamespace

from crewai import Process

from invest_research.agents.crew_factory import _task_output_loader, build_research_crew
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
    assert crew.tasks[0].output_pydantic is None  # P05.5-fix：research 由 runner 解析+收尾
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


def _stub_task(pack: object) -> SimpleNamespace:
    """构造简化上游 Task（只有 output.pydantic，模拟 CrewAI sequential 执行后状态）。"""
    return SimpleNamespace(output=SimpleNamespace(pydantic=pack, raw=""))


def test_task_output_loader_reads_upstream_pydantic() -> None:
    """方案B：loader 从上游 Task 的 output.pydantic 读取 pack 真实内容。"""
    research_task = _stub_task(_research_pack())
    analysis_task = _stub_task(_analysis_pack())
    loader = _task_output_loader(research_task, analysis_task)

    research_content = loader("research_pack")
    assert research_content is not None
    assert research_content["version"] == "research_pack_v1"
    assert research_content["company_identity"]["cik"] == "0000789019"

    analysis_content = loader("analysis_pack")
    assert analysis_content is not None
    assert analysis_content["version"] == "analysis_pack_v1"
    assert analysis_content["facts"][0]["concept"] == "Revenue"


def test_task_output_loader_unknown_key_and_no_output() -> None:
    """方案B：未知 key 或不存在的上游 task 返回 None（不伪造内容）。"""
    research_task = _stub_task(_research_pack())
    loader = _task_output_loader(research_task)

    assert loader("unknown_pack") is None  # 未识别 key
    assert loader("analysis_pack") is None  # 只给了 research 一个上游


def test_task_output_loader_returns_none_when_output_missing() -> None:
    """方案B：上游 Task 无 output（尚未执行/执行失败）时返回 None。"""
    loader = _task_output_loader(SimpleNamespace(output=None), SimpleNamespace(output=None))
    assert loader("research_pack") is None
    assert loader("analysis_pack") is None


def _stub_task_json_dict(data: dict[str, object]) -> SimpleNamespace:
    """构造只有 json_dict（无 pydantic/raw）的 TaskOutput，覆盖 Research 未绑模型路径。"""
    return SimpleNamespace(output=SimpleNamespace(pydantic=None, raw="", json_dict=data))


def test_task_output_loader_reads_json_dict_without_pydantic() -> None:
    """方案B：上游未绑 output_pydantic 时（Research），通过 json_dict 读取真实内容。"""
    research_data = {
        "version": "research_pack_v1",
        "company_identity": {"cik": "0000789019", "legal_name": "Microsoft Corp"},
        "as_of_date": "2025-12-31",
        "sources": [],
    }
    research_task = _stub_task_json_dict(research_data)
    loader = _task_output_loader(research_task)

    content = loader("research_pack")
    assert content is not None
    assert content["version"] == "research_pack_v1"
    assert content["company_identity"]["cik"] == "0000789019"
