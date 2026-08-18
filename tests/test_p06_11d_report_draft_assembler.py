"""P06-11D：DeepSeek Writer 普通文本 → ReportDraftAssembler 确定性组装（离线测试）。

验证目标（docs/05 P06-11D 验收，20 项契约）：
1. 合法 Markdown → ReportDraft；
2. version 由代码固定（禁止模型生成）；
3. title 从可信公司身份生成（legal_name/ticker + as_of_date）；
4. citation_keys 确定性提取（只收正文中实际出现的可信候选键）；
5. 空文本拒绝（REPORT_INVALID）；
6. 过短说明拒绝（REPORT_INVALID）；
7. Tool Action 拒绝（REPORT_INVALID）；
8. Action Input 拒绝（REPORT_INVALID）；
9. “无法生成报告”等拒绝文本不能通过（REPORT_INVALID）；
10. 截断输出拒绝（REPORT_TRUNCATED，finish_reason=length 必须拒绝）；
11. 缺必要章节拒绝，或进入现有质量门禁（assembler 只要求至少一个章节，
    完整章节缺失由 Quality Gate REVISE）；
12. Markdown 中引号、换行、表格不需要 JSON 转义（原文保留）；
13. DeepSeek Writer 不再要求输出完整 ReportDraft JSON（提示词不再含 JSON 约束）；
14. DeepSeek 不触发 beta.chat.completions.parse（不绑 output_pydantic/output_json）；
15. Writer 读取的是 P06-11C 组装后的 FinancialAnalysisPack（loader 去草稿键）；
16. Qwen 路径回归（继续绑定 output_pydantic=ReportDraft）；
17. 最终 08_report.md/09_report.pdf 发布回归（ReportArtifactPublisher 消费 ReportDraft）；
18. 错误消息脱敏（不含 API Key / 完整报告正文）；
19. 不打印完整模型报告（assembler 错误只含稳定码+简短原因）；
20. 一次 fake 全链产生合法 ReportDraft（LiveResearchFlowRunner + 注入 fake crew）。

全程使用 FakeLLM / 本地 Pydantic / 本地 fixture，禁止真实联网。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from invest_research.agents.crew_factory import _task_output_loader
from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.agents.writer_task import build_writer_task
from invest_research.application.analysis_assembler import build_fact_ref
from invest_research.application.report_draft_assembler import (
    ReportAssemblerError,
    ReportDraftAssembler,
    _source_citation_key,
    build_report_title,
)
from invest_research.domain.errors import ErrorCode, is_retryable, is_terminal_failure
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnalysisSelectionDraft,
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.flow_wiring import (
    LiveFlowExecutionError,
    LiveResearchFlowRunner,
)
from invest_research.reporting.artifact_publisher import ReportArtifactPublisher

_TEST_API_KEY = "sk-test-placeholder"

FIXTURE = Path(__file__).parent / "fixtures" / "companyfacts_msft.json"


def _config(vendor: str = "deepseek") -> LLMConfig:
    """构造供应商测试配置（vendor 仅限 qwen/deepseek/generic）。"""
    return LLMConfig(
        provider="openai_compatible",
        vendor=vendor,  # type: ignore[arg-type]
        base_url="https://example.com/v1",
        api_key=SecretStr(_TEST_API_KEY),
        model_research=f"{vendor}-research",
        model_analysis=f"{vendor}-analysis",
        model_writer=f"{vendor}-writer",
        temperature=0.2,
        timeout=60.0,
        enable_thinking=None,
    )


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))


def _identity() -> CompanyIdentity:
    return CompanyIdentity(cik="0000789019", ticker="MSFT", legal_name="Microsoft Corp")


def _source() -> Source:
    return Source(
        source_type=SourceType.SEC_FILING,
        canonical_url="https://www.sec.gov/Archives/edgar/data/0000789019/10-K",
        title="10-K filed 2025-10-31",
        accessed_at=date(2025, 10, 31),
    )


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=_identity(),
        as_of_date=date(2025, 12, 31),
        sources=[_source()],
    )


def _analysis_pack() -> FinancialAnalysisPack:
    fact = FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Revenue",
        value=Decimal("245100000000"),
        unit="USD",
        period_start=date(2024, 7, 1),
        period_end=date(2025, 6, 30),
    )
    return FinancialAnalysisPack(
        version="analysis_pack_v2",
        schema_version="analysis_pack_v2",
        period_end=date(2025, 6, 30),
        facts=[fact],
        completeness=AnalysisCompleteness.COMPLETE,
    )


def _valid_markdown() -> str:
    """合法报告正文：超过最小长度、含必需章节、含 citation key。"""
    return (
        "## 执行摘要\n"
        "本报告基于已核实的 SEC 申报文件整理，仅用于技术演示。"
        "本报告不构成任何投资建议，也不包含任何收益承诺。\n"
        "## 公司与业务概览\n"
        "Microsoft Corp 是一家跨国科技公司，主要业务包括生产力软件、"
        "智能云与企业服务、个人计算设备等。\n"
        "## 财务表现\n"
        "营业收入为 2451 亿美元，相关引用见 src_abc123。"
        "本节所有数据均来自上游 FinancialAnalysisPack，未做任何推断。\n"
        "## 风险因素与催化因素\n"
        "市场与监管风险并存，包括宏观经济波动、竞争加剧与合规要求变化等。\n"
        "## 数据限制\n"
        "数据仅截至指定数据截止日，超过该日期的信息不在本报告范围内。\n"
        "## 来源清单与非投资建议声明\n"
        "本报告不构成任何投资建议。投资者应直接查阅 SEC 原始文件。\n"
    )


# ---------------------------------------------------------------------------
# 1~4. 合法性 / version / title / citation_keys
# ---------------------------------------------------------------------------


def test_valid_markdown_assembles_to_report_draft() -> None:
    """合法 Markdown → ReportDraft。"""
    draft = ReportDraftAssembler().assemble(
        _valid_markdown(), _request(), _research_pack(), _analysis_pack()
    )
    assert isinstance(draft, ReportDraft)
    assert draft.version == "report_draft_v1"
    # assemble 对输入做 strip()，去掉首尾空白
    assert draft.markdown == _valid_markdown().strip()


def test_version_fixed_by_code_not_model() -> None:
    """version 由代码固定为 report_draft_v1（模型无法修改）。"""
    draft = ReportDraftAssembler().assemble(
        _valid_markdown(), _request(), _research_pack(), _analysis_pack()
    )
    assert draft.version == "report_draft_v1"


def test_title_from_trusted_company_identity() -> None:
    """title 从可信公司身份（legal_name/ticker + as_of_date）确定性生成。"""
    draft = ReportDraftAssembler().assemble(
        _valid_markdown(), _request(), _research_pack(), _analysis_pack()
    )
    assert draft.title == "Microsoft Corp（MSFT）（数据截止 2025-12-31）投资研究初稿"


def test_build_report_title_without_ticker() -> None:
    """ticker 缺失时标题省略括号部分。"""
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    title = build_report_title(identity, date(2025, 12, 31), fallback="MSFT")
    assert title == "Microsoft Corp（数据截止 2025-12-31）投资研究初稿"


def test_citation_keys_extracted_deterministically() -> None:
    """citation_keys 只收集正文中实际出现的可信候选键。"""
    src_key = _source_citation_key(_source())
    md = _valid_markdown().replace("src_abc123", src_key)
    draft = ReportDraftAssembler().assemble(md, _request(), _research_pack(), _analysis_pack())
    assert src_key in draft.citation_keys
    # 模型伪造的键不在候选集合内（不会被提取）
    md_with_fake = md + "\n引用 [伪造键 fake_claim_123] 不被提取\n"
    draft2 = ReportDraftAssembler().assemble(
        md_with_fake, _request(), _research_pack(), _analysis_pack()
    )
    assert all("fake_claim_123" not in key for key in draft2.citation_keys)


def test_model_cannot_forge_citation_key() -> None:
    """模型无法伪造 citation key（伪造键不在可信候选集合）。"""
    md = _valid_markdown() + "\n见 [src_ffffffffffff] 伪造引用。\n"
    draft = ReportDraftAssembler().assemble(md, _request(), _research_pack(), _analysis_pack())
    assert all(key != "src_ffffffffffff" for key in draft.citation_keys)


# ---------------------------------------------------------------------------
# 5~6. 空文本 / 过短说明
# ---------------------------------------------------------------------------


def test_empty_text_rejected() -> None:
    """空文本 → REPORT_INVALID。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble("", _request(), _research_pack(), _analysis_pack())
    assert exc.value.error_code == "REPORT_INVALID"
    assert not is_retryable(ErrorCode.REPORT_INVALID)


def test_short_explanation_rejected() -> None:
    """过短说明（如“好的，我来写”）→ REPORT_INVALID。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            "好的，我来写", _request(), _research_pack(), _analysis_pack()
        )
    assert exc.value.error_code == "REPORT_INVALID"


# ---------------------------------------------------------------------------
# 7~9. Tool Action / Action Input / 拒绝文本
# ---------------------------------------------------------------------------


def test_tool_action_rejected() -> None:
    """Tool Action 参数不能当报告正文。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            "Action: WriterContextReader\nAction Input: {}",
            _request(),
            _research_pack(),
            _analysis_pack(),
        )
    assert exc.value.error_code == "REPORT_INVALID"


def test_action_input_rejected() -> None:
    """Action Input 参数不能当报告正文。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            "Action Input: {\"artifact_key\": \"research_pack\"}",
            _request(),
            _research_pack(),
            _analysis_pack(),
        )
    assert exc.value.error_code == "REPORT_INVALID"


def test_writer_plain_text_fails_fast_in_flow_wiring(tmp_path: Path) -> None:
    """runner 对普通自然语言 Writer 输出不再抛 NOT_A_PACK，而是 REPORT_INVALID。"""
    config = LLMConfig(
        provider="openai_compatible",
        vendor="deepseek",
        base_url="https://example.com/v1",
        api_key=SecretStr(_TEST_API_KEY),
        model_research="deepseek-r",
        model_analysis="deepseek-a",
        model_writer="deepseek-w",
    )
    research = _research_pack()
    analysis = _analysis_pack()

    class _ShortCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(tasks_output=[research, analysis, "抱歉，我无法生成报告。"])

    runner = LiveResearchFlowRunner(
        config=config,
        artifact_root=str(tmp_path),
        crew_factory=lambda cfg, rt: _ShortCrew(),  # type: ignore[no-any-return]
    )
    with pytest.raises(LiveFlowExecutionError) as exc_info:
        runner.run(_request())
    assert exc_info.value.error_code == "REPORT_INVALID"
    assert exc_info.value.failure_stage == "05_writer"


def test_cannot_generate_phrase_rejected() -> None:
    """“无法生成报告”等拒绝文本不能通过。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            "抱歉，我无法生成报告。", _request(), _research_pack(), _analysis_pack()
        )
    assert exc.value.error_code == "REPORT_INVALID"


# ---------------------------------------------------------------------------
# 10. 截断输出拒绝
# ---------------------------------------------------------------------------


def test_finish_reason_length_rejected() -> None:
    """finish_reason=length → REPORT_TRUNCATED（明显截断）。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            _valid_markdown(),
            _request(),
            _research_pack(),
            _analysis_pack(),
            finish_reason="length",
        )
    assert exc.value.error_code == "REPORT_TRUNCATED"
    assert not is_retryable(ErrorCode.REPORT_TRUNCATED)


def test_trailing_truncation_rejected() -> None:
    """正文末尾未完标志 → REPORT_TRUNCATED。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            _valid_markdown() + "\n公司后续发展……",
            _request(),
            _research_pack(),
            _analysis_pack(),
        )
    assert exc.value.error_code == "REPORT_TRUNCATED"


# ---------------------------------------------------------------------------
# 11. 缺必要章节 → 拒绝（assembler 至少要求一个章节）或进入质量门禁
# ---------------------------------------------------------------------------


def test_missing_all_sections_rejected() -> None:
    """随意长文本（不含任何必需章节）→ REPORT_INVALID。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            "今日天气晴朗，适合出行。" * 40, _request(), _research_pack(), _analysis_pack()
        )
    assert exc.value.error_code == "REPORT_INVALID"


def test_partial_sections_enter_quality_gate() -> None:
    """包含至少一个必需章节 → 组装成功，完整章节缺失由 Quality Gate REVISE。"""
    md = "## 执行摘要\n内容\n" + "其它正文" * 50
    draft = ReportDraftAssembler().assemble(md, _request(), _research_pack(), _analysis_pack())
    assert isinstance(draft, ReportDraft)


# ---------------------------------------------------------------------------
# 12. Markdown 引号/换行/表格无需 JSON 转义
# ---------------------------------------------------------------------------


def test_markdown_quotes_tables_no_json_escape() -> None:
    """正文中的引号、换行、表格原样保留（无需 JSON 转义）。"""
    md = _valid_markdown() + (
        "\n| 指标 | 数值 |\n| --- | --- |\n| 收入 | \"2451亿\" |\n"
        "他说：\"数据已核实\"（含换行\n第二行）\n"
    )
    draft = ReportDraftAssembler().assemble(md, _request(), _research_pack(), _analysis_pack())
    assert draft.markdown == md.strip()
    assert '"2451亿"' in draft.markdown
    assert "| 指标 | 数值 |" in draft.markdown


# ---------------------------------------------------------------------------
# 13~14. DeepSeek Writer 不再要求 JSON；不触发 beta
# ---------------------------------------------------------------------------


def test_deepseek_writer_prompt_no_longer_requires_full_report_json() -> None:
    """DeepSeek Writer 提示词只要求 Markdown 正文，不再要求完整 ReportDraft JSON。"""
    config = _config("deepseek")
    task = build_writer_task(config, fake=FakeLLM(config=config, role=LLMRole.WRITER))
    assert task.output_pydantic is None
    assert task.output_json is None
    assert "直接输出一份完整的 Markdown 报告正文" in task.description
    assert "report_draft_v1" not in task.description  # version 不用模型生成
    assert "最终答案只能是一个 JSON object" not in task.description


def test_deepseek_no_beta_parse() -> None:
    """DeepSeek 不触发 beta.chat.completions.parse（Task 不绑 output_pydantic/output_json）。"""
    config = _config("deepseek")
    task = build_writer_task(config, fake=FakeLLM(config=config, role=LLMRole.WRITER))
    assert task.output_pydantic is None
    assert task.output_json is None


# ---------------------------------------------------------------------------
# 15. Writer 读取组装后的 FinancialAnalysisPack
# ---------------------------------------------------------------------------


def test_writer_loader_reads_assembled_analysis_pack() -> None:
    """Writer 的 analysis_pack loader 读到的必须是组装后的完整 pack（非草稿）。"""
    fact = FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Revenue",
        value=Decimal("245100000000"),
        unit="USD",
        period_start=date(2024, 7, 1),
        period_end=date(2025, 6, 30),
    )
    ref = build_fact_ref(fact)
    real_draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        schema_version="analysis_selection_draft_v1",
        period_end=date(2025, 6, 30),
        selected_fact_refs=[ref],
        completeness=AnalysisCompleteness.COMPLETE,
    )

    class _DraftTask:
        output = SimpleNamespace(
            pydantic=real_draft,
            json_dict=real_draft.model_dump(mode="json"),
            exported_output=real_draft.model_dump(mode="json"),
            raw=real_draft.model_dump_json(),
        )

    loader = _task_output_loader(None, _DraftTask(), analysis_facts=[fact])  # type: ignore[arg-type]
    content = loader("analysis_pack")
    assert content is not None
    assert "selected_fact_refs" not in content
    assert "facts" in content
    assert content["facts"][0]["company_id"] == "0000789019"


# ---------------------------------------------------------------------------
# 16. Qwen 路径回归
# ---------------------------------------------------------------------------


def test_qwen_writer_task_keeps_native_pydantic() -> None:
    """Qwen Writer Task 继续绑定 output_pydantic=ReportDraft（原路径不受影响）。"""
    config = _config("qwen")
    task = build_writer_task(config, fake=FakeLLM(config=config, role=LLMRole.WRITER))
    assert task.output_pydantic is ReportDraft
    assert task.output_json is None
    assert "直接输出一份完整的 Markdown 报告正文" not in task.description


# ---------------------------------------------------------------------------
# 17. 最终 08_report.md/09_report.pdf 发布回归
# ---------------------------------------------------------------------------


def test_report_artifact_publisher_publishes_assembled_draft(tmp_path: Path) -> None:
    """ReportArtifactPublisher 消费组装后的 ReportDraft 能产生 08/09 工件。"""
    state = ResearchFlowState(request=_request())
    state.research_pack = _research_pack()
    state.analysis_pack = _analysis_pack()
    state.report_draft = ReportDraftAssembler().assemble(
        _valid_markdown(), _request(), _research_pack(), _analysis_pack()
    )

    publisher = ReportArtifactPublisher(tmp_path)
    job_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    metadata = publisher.publish(job_id, state)
    keys = [m["artifact_key"] for m in metadata]
    assert "08_report.md" in keys
    assert "09_report.pdf" in keys
    assert (tmp_path / str(job_id) / "08_report.md").exists()
    assert (tmp_path / str(job_id) / "09_report.pdf").exists()


# ---------------------------------------------------------------------------
# 18~19. 错误消息脱敏 / 不打印完整报告
# ---------------------------------------------------------------------------


def test_assembler_error_does_not_leak_full_report() -> None:
    """assembler 异常只含稳定错误码与简短原因，不含完整报告正文。"""
    rejected_text = "抱歉，我无法生成报告。" + ("补充细节。" * 60)  # 超过最小长度
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            rejected_text, _request(), _research_pack(), _analysis_pack()
        )
    message = str(exc.value)
    assert message == "Writer 输出是拒绝/工具说明，不是报告正文"
    assert rejected_text not in message  # 不打印完整模型报告正文
    # 不打印完整模型报告正文
    md = _valid_markdown()
    with pytest.raises(ReportAssemblerError) as exc2:
        ReportDraftAssembler().assemble(
            md, _request(), _research_pack(), _analysis_pack(), finish_reason="length"
        )
    assert md not in str(exc2.value)


def test_assembler_error_does_not_contain_api_key() -> None:
    """assembler 错误消息不含 API Key。"""
    with pytest.raises(ReportAssemblerError) as exc:
        ReportDraftAssembler().assemble(
            "抱歉，我无法生成报告。", _request(), _research_pack(), _analysis_pack()
        )
    assert _TEST_API_KEY not in str(exc.value)
    assert "sk-" not in str(exc.value)


def test_report_errors_are_terminal_failures() -> None:
    """REPORT_INVALID / REPORT_TRUNCATED 均为不可重试终态。"""
    assert is_terminal_failure(ErrorCode.REPORT_INVALID)
    assert is_terminal_failure(ErrorCode.REPORT_TRUNCATED)
    assert not is_retryable(ErrorCode.REPORT_INVALID)
    assert not is_retryable(ErrorCode.REPORT_TRUNCATED)


# ---------------------------------------------------------------------------
# 20. 一次 fake 全链产生合法 ReportDraft
# ---------------------------------------------------------------------------


def test_fake_full_chain_produces_valid_report_draft(tmp_path: Path) -> None:
    """LiveResearchFlowRunner + 注入 fake crew：writer 输出普通 Markdown → ReportDraft。"""
    config = LLMConfig(
        provider="openai_compatible",
        vendor="deepseek",
        base_url="https://example.com/v1",
        api_key=SecretStr(_TEST_API_KEY),
        model_research="deepseek-research",
        model_analysis="deepseek-analysis",
        model_writer="deepseek-writer",
    )

    research = _research_pack()
    analysis = _analysis_pack()
    writer_markdown = _valid_markdown()

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(tasks_output=[research, analysis, writer_markdown])

    runner = LiveResearchFlowRunner(
        config=config,
        artifact_root=str(tmp_path),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    state = runner.run(_request())

    assert state.report_draft is not None
    assert state.report_draft.version == "report_draft_v1"
    assert state.report_draft.title == "Microsoft Corp（MSFT）（数据截止 2025-12-31）投资研究初稿"
    assert state.report_draft.markdown == writer_markdown.strip()
    assert state.analysis_pack is not None
    assert state.research_pack is not None
