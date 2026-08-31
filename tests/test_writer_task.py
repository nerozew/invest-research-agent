"""P03-07 Writer Task 测试（fake LLM，不联网）。

验证目标（docs/05 P03-07 验收）：
- Agent 可构建，只暴露 WriterContextReader（一次聚合读取，最小权限白名单）；
- Task 绑定 output_pydantic=ReportDraft；
- 三个工具是确定性实现（不依赖 LLM）；
- FakeLLM 可通过 response_model=ReportDraft 实例化报告草稿；
- pair 返回同一 Agent 实例。
"""

from __future__ import annotations

from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.agents.writer_task import (
    REPORT_SECTIONS,
    build_writer_agent,
    build_writer_pair,
    build_writer_task,
    citation_verifier,
    make_artifact_reader,
    make_writer_context_reader,
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
    """主 Writer 只暴露一次聚合读取工具，避免确定性工具循环耗尽预算。"""
    agent = build_writer_agent(_config(), fake=_fake())
    tool_names = {t.name for t in agent.tools or []}
    assert tool_names == {"WriterContextReader"}
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
    """ArtifactReader：未注入 loader 时确定性返回工件可读状态（占位兼容）。"""
    reader = make_artifact_reader()
    result = reader.run(artifact_key="02_research_pack.json")
    assert result["status"] == "readable"
    assert result["artifact_key"] == "02_research_pack.json"


def test_artifact_reader_with_loader_returns_real_content() -> None:
    """方案B：注入真实 loader 时 ArtifactReader 返回上游 pack 真实内容。"""
    loader_calls: list[str] = []

    def loader(artifact_key: str) -> dict[str, object] | None:
        loader_calls.append(artifact_key)
        if artifact_key != "research_pack":
            return None
        return {
            "version": "research_pack_v1",
            "company": {"ticker": "AAPL", "name": "Apple Inc."},
        }

    reader = make_artifact_reader(loader)
    result = reader.run(artifact_key="research_pack")
    assert result["status"] == "readable"
    assert result["artifact_key"] == "research_pack"
    assert result["content"]["company"]["ticker"] == "AAPL"
    assert loader_calls == ["research_pack"]

    # 未知 key → loader 返回 None → not_found（Writer 可在数据限制中说明，不编造）
    not_found = reader.run(artifact_key="unknown_pack")
    assert not_found["status"] == "not_found"
    assert loader_calls == ["research_pack", "unknown_pack"]


def test_artifact_reader_loader_returns_none_is_not_found() -> None:
    """方案B：loader 返回 None 时 ArtifactReader 明确 not_found（不静默给空壳）。"""
    reader = make_artifact_reader(lambda _key: None)
    result = reader.run(artifact_key="analysis_pack")
    assert result["status"] == "not_found"
    assert "content" not in result


def test_writer_context_reader_returns_both_packs_and_template_once() -> None:
    """聚合 Reader 一次返回两个 Pack 与模板，缺失项显式为 partial。"""
    calls: list[str] = []

    def loader(key: str) -> dict[str, object] | None:
        calls.append(key)
        if key == "research_pack":
            return {"version": "research_pack_v1"}
        if key == "analysis_pack":
            return {"version": "analysis_pack_v2", "completeness": "partial"}
        return None

    reader = make_writer_context_reader(loader)
    result = reader.run()
    assert result["status"] == "ready"
    assert result["research_pack"]["version"] == "research_pack_v1"
    assert result["analysis_pack"]["version"] == "analysis_pack_v2"
    assert result["required_sections"] == list(REPORT_SECTIONS)
    assert result["missing"] == []
    assert calls == ["research_pack", "analysis_pack"]

    partial = make_writer_context_reader(lambda key: None if key == "analysis_pack" else {})
    partial_result = partial.run()
    assert partial_result["status"] == "partial"
    assert partial_result["missing"] == ["analysis_pack"]


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
