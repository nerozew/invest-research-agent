"""P03-07 Writer Task 测试（fake LLM，不联网）。

验证目标（docs/05 P03-07 验收）：
- Agent 可构建，只暴露 ArtifactReader + CitationVerifier + TemplateGuide（最小权限白名单）；
- Task 绑定 output_pydantic=ReportDraft；
- 三个工具是确定性实现（不依赖 LLM）；
- FakeLLM 可通过 response_model=ReportDraft 实例化报告草稿；
- pair 返回同一 Agent 实例。
"""

from __future__ import annotations

from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.agents.writer_task import (
    REPORT_SECTIONS,
    artifact_reader,
    build_writer_agent,
    build_writer_pair,
    build_writer_task,
    citation_verifier,
    template_guide,
)
from invest_research.domain.models import ReportDraft
from invest_research.settings import Settings


def _config() -> LLMConfig:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    return LLMConfig.from_settings(settings)


def _fake() -> FakeLLM:
    return FakeLLM(config=_config(), role=LLMRole.WRITER)


def test_writer_agent_exposes_only_whitelisted_tools() -> None:
    """最小权限白名单：Writer 只暴露 ArtifactReader + CitationVerifier + TemplateGuide。"""
    agent = build_writer_agent(_config(), fake=_fake())
    tool_names = {t.name for t in agent.tools or []}
    assert tool_names == {"ArtifactReader", "CitationVerifier", "TemplateGuide"}
    # 绝不暴露搜索/计算等无关工具
    assert not tool_names & {"GoogleSearch", "FinancialCalculator", "SECSubmissions"}


def test_writer_task_binds_output_pydantic() -> None:
    """Task 绑定 output_pydantic=ReportDraft（类，非实例）。"""
    task = build_writer_task(_config(), fake=_fake())
    assert task.output_pydantic is ReportDraft


def test_writer_pair_returns_same_agent_instance() -> None:
    """pair 复用同一 Agent 实例（task.agent is agent）。"""
    fake = _fake()
    agent, task = build_writer_pair(_config(), fake=fake)
    assert task.agent is agent


def test_template_guide_returns_known_sections() -> None:
    """TemplateGuide：返回 PRD §7 标准章节，且能识别未知章节。"""
    result = template_guide.run()
    assert result["sections"] == list(REPORT_SECTIONS)
    assert "非投资建议" in str(result["sections"])
    known = template_guide.run(section_name="财务表现")
    assert known["is_known"] is True
    unknown = template_guide.run(section_name="买入建议")
    assert unknown["is_known"] is False


def test_citation_verifier_is_deterministic() -> None:
    """CitationVerifier：未提供 source → 明确失败（不靠 LLM 猜）。"""
    result = citation_verifier.run(claim="微软收入 2451 亿美元", key_numbers=["245100000000"])
    assert result["valid"] is False  # 无 source/locator → MISSING_SOURCE


def test_artifact_reader_returns_readable() -> None:
    """ArtifactReader：确定性返回工件可读状态。"""
    result = artifact_reader.run(artifact_key="02_research_pack.json")
    assert result["status"] == "readable"
    assert result["artifact_key"] == "02_research_pack.json"


def test_fake_llm_instantiates_report_draft() -> None:
    """fake LLM 通过 response_model 实例化 ReportDraft。"""
    draft = ReportDraft(
        version="report_draft_v1",
        title="微软（MSFT）投资研究初稿",
        markdown="# 微软\n\n> 非投资建议",
        citation_keys=["claim-1"],
    )
    fake = FakeLLM(config=_config(), role=LLMRole.WRITER, responses=[draft])
    result = fake.call(messages="请写报告", response_model=ReportDraft)
    assert isinstance(result, ReportDraft)
    assert result.version == "report_draft_v1"
    assert result.title.startswith("微软")


def test_build_writer_agent_without_fake_uses_real_llm() -> None:
    """P05-12B：未传 fake 时构造真实 LLM（不联网，仅构造），不再抛 NotImplementedError。"""
    agent = build_writer_agent(_config(), fake=None)
    assert agent.role == "报告撰写 Agent"
    assert agent.llm is not None
    # 真实构造不发起任何网络请求
    assert "sk-test-placeholder" not in repr(agent.llm)
