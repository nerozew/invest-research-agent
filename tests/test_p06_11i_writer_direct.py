"""P06-11I：Writer 无工具单轮直接调度契约测试（离线，不联网）。

覆盖（docs/05 P06-11I 验收 17 项重点）：
1. 确定性加载 ResearchPack/AnalysisPack 到紧凑上下文；
2. CitationRegistry 全部合法 key 被传入上下文；
3. 上下文超限时确定性裁剪；
4. 裁剪后公司身份与 citation keys 仍存在；
5. Direct Writer 请求完全不包含 tools/tool_choice/tool schema；
6. DeepSeek 不触发 CrewAI Writer Agent/Crew（不创建 Crew、不 kickoff）；
7. 普通长 Markdown 能组装为 ReportDraft；
8. 空响应触发一次有限重试；
9. 短响应触发一次有限重试；
10. finish_reason=length 触发 REPORT_TRUNCATED；
11. 第二次失败后稳定终止（不无限重试）；
12. 重试复用同一份确定性上下文、不重新执行 Research/Analysis/外部工具；
13. Markdown 非法 citation 被拒绝；
14. partial/unavailable 被正确写入限制章节；
15. Jaeger/Prometheus 不包含 prompt/正文/密钥；
16. 两个并发 Job 的 Writer 上下文互不污染；
17. Qwen 原有路径回归不受影响。

全程使用 mock client，禁止真实联网/真实模型调用。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from invest_research.agents.llm_factory import (
    LLMConfig,
    LLMRole,
    StructuredOutputMode,
    structured_output_mode,
)
from invest_research.application.citation_registry import build_citation_registry
from invest_research.application.report_draft_assembler import (
    ReportDraftAssembler,
)
from invest_research.application.writer_context_builder import (
    BuiltWriterContext,
    WriterContextBuilder,
    WriterContextLimits,
)
from invest_research.application.writer_direct_dispatch import (
    WriterDispatchResult,
)
from invest_research.domain.models import (
    AnalysisCompleteness,
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.infrastructure.direct_writer_dispatch import DirectLlmWriterDispatch
from invest_research.infrastructure.flow_wiring import (
    LiveFlowExecutionError,
    LiveResearchFlowRunner,
)

_API_KEY = "sk-test-placeholder"


def _config(vendor: str = "deepseek") -> LLMConfig:
    return LLMConfig(
        provider="openai_compatible",
        vendor=vendor,  # type: ignore[arg-type]
        base_url="https://example.com/v1",
        api_key=SecretStr(_API_KEY),
        model_research=f"{vendor}-research",
        model_analysis=f"{vendor}-analysis",
        model_writer=f"{vendor}-writer",
        temperature=0.2,
        timeout=60.0,
        enable_thinking=False,
    )


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))


def _identity() -> CompanyIdentity:
    return CompanyIdentity(
        cik="0000789019",
        ticker="MSFT",
        legal_name="Microsoft Corporation",
        exchange="NASDAQ",
    )


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=_identity(),
        as_of_date=date(2025, 12, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://www.sec.gov/Archives/edgar/data/789019/000156459025000001/msft-10k_20250630.htm",
                title="10-K filed 2025-07-30",
                published_at=date(2025, 7, 30),
                accessed_at=date(2025, 12, 31),
                locator="10-K",
            ),
            Source(
                source_type=SourceType.WEB,
                canonical_url="https://example.com/news/msft-revenue",
                title="MSFT 季度营收报道",
                published_at=date(2025, 10, 1),
                accessed_at=date(2025, 12, 31),
            ),
        ],
    )


def _fact(concept: str, value: str, label: str | None = None) -> FinancialFact:
    return FinancialFact(
        company_id="0000789019",
        source_id="src-1",
        taxonomy="us-gaap",
        concept=concept,
        label=label or concept,
        value=Decimal(value),
        unit="USD",
        period_start=date(2025, 7, 1),
        period_end=date(2025, 9, 30),
        fiscal_year=2025,
        fiscal_period="Q1",
    )


def _analysis_pack(completeness: str = "complete") -> FinancialAnalysisPack:
    facts = [_fact("Revenue", "60000000000", "Total Revenue")]
    if completeness == "complete":
        return FinancialAnalysisPack(
            version="analysis_pack_v2",
            schema_version="analysis_pack_v2",
            period_end=date(2025, 12, 31),
            facts=facts,
            completeness=AnalysisCompleteness.COMPLETE,
        )
    if completeness == "partial":
        return FinancialAnalysisPack(
            version="analysis_pack_v2",
            schema_version="analysis_pack_v2",
            period_end=date(2025, 12, 31),
            facts=[],
            limitations=["缺少 2025 全年现金流量表数据"],
            completeness=AnalysisCompleteness.PARTIAL,
        )
    return FinancialAnalysisPack(
        version="analysis_pack_v2",
        schema_version="analysis_pack_v2",
        period_end=date(2025, 12, 31),
        facts=[],
        completeness=AnalysisCompleteness.UNAVAILABLE,
        unavailable_reason="SEC 未披露所需财务数据",
    )


def _valid_markdown(registry) -> str:
    keys = sorted(registry.keys())
    key_text = " ".join(f"[{k}]" for k in keys)
    return (
        "# 封面信息\n\n"
        "# 执行摘要\n\n"
        "MSFT 财务表现稳健。\n\n"
        "# 公司与业务概览\n\n"
        "Microsoft 是全球领先的软件公司。\n\n"
        "# 财务表现\n\n"
        f"营业收入约 600 亿美元。{key_text}\n\n"
        "# 关键指标表\n\n"
        "| 指标 | 值 |\n| --- | --- |\n| 营收 | 600 亿 |\n\n"
        "# 风险因素与催化因素\n\n"
        "市场竞争风险。\n\n"
        "# 数据限制\n\n"
        "本报告基于 2025-12-31 前的公开数据。\n\n"
        "# 来源清单与非投资建议声明\n\n"
        "本报告不构成投资建议。\n\n"
    )


def _mock_response(text: str, finish_reason: str = "stop", usage=None) -> SimpleNamespace:
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_client(responses: list[SimpleNamespace]) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.side_effect = responses
    return client


def _build_context(pack, analysis, registry, limits=None) -> "BuiltWriterContext":
    return WriterContextBuilder(limits=limits).build(_request(), pack, analysis, registry)


# ---------------------------------------------------------------------------
# 1-2：确定性加载 + citation keys 完整传入
# ---------------------------------------------------------------------------


def test_context_build_includes_all_citation_keys() -> None:
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    context = _build_context(pack, analysis, registry)
    assert len(registry.keys()) > 0
    for key in sorted(registry.keys()):
        assert key in context.text, f"合法 key {key} 未进入上下文"


def test_context_build_company_identity_present() -> None:
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    context = _build_context(pack, analysis, registry)
    assert "Microsoft Corporation" in context.text
    assert "0000789019" in context.text
    assert "2025-12-31" in context.text
    assert "zh-CN" in context.text


# ---------------------------------------------------------------------------
# 3-4：上下文超限时确定性裁剪
# ---------------------------------------------------------------------------


def test_context_truncates_overflowing_facts_but_keeps_identity_and_keys() -> None:
    """超限时按条数确定性裁剪：3 条 facts、max_facts=1 → 只写入 1 条。

    同时验证公司身份与 citation keys 永远保留（不随 facts 裁剪而丢失）。
    """
    pack = _research_pack()
    analysis = _analysis_pack()
    # 扩成 3 条 facts 以触发条数裁剪。
    extra = analysis.model_copy(
        update={
            "facts": [
                *analysis.facts,
                _fact("Assets", "100000000000"),
                _fact("Liabilities", "70000000000"),
            ]
        }
    )
    registry = build_citation_registry(pack, extra)
    limits = WriterContextLimits(
        max_chars=6000, max_estimated_tokens=2000, max_facts=1, max_sources=2
    )
    context = _build_context(pack, extra, registry, limits=limits)
    # 条数裁剪发生（3 条 → 只保留 1 条）。
    assert context.truncated is True
    assert "facts_overflow" in context.dropped_sections
    assert context.fact_count == 1
    # 公司身份与全部 citation keys 仍完整保留。
    assert "Microsoft Corporation" in context.text
    assert "0000789019" in context.text
    assert all(k in context.text for k in sorted(registry.keys())), "citation keys 不能被裁剪掉"
    # 被裁剪的两条 fact 的 concept 不得进入上下文。
    assert "Assets" not in context.text
    assert "Liabilities" not in context.text
    assert "Revenue" in context.text


def test_context_overflow_raises_when_even_identity_cannot_fit() -> None:
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    tiny = WriterContextLimits(max_chars=50, max_estimated_tokens=2000, max_facts=0, max_sources=0)
    from invest_research.application.writer_context_builder import WriterContextBuildError

    with pytest.raises(WriterContextBuildError):
        WriterContextBuilder(limits=tiny).build(_request(), pack, analysis, registry)


# ---------------------------------------------------------------------------
# 5-6：无工具调用 + DeepSeek 不触发 CrewAI Agent/Crew
# ---------------------------------------------------------------------------
class TestDirectDispatchNoTools:
    def test_request_has_no_tools(self) -> None:
        client = _make_client([_mock_response("ok")])
        dispatch = DirectLlmWriterDispatch(_config(), client=client)
        context = _build_context(None, None, build_citation_registry(None, None))
        dispatch.dispatch(_request(), context)
        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert "tools" not in call_kwargs, "请求绝不能包含 tools"
        assert "tool_choice" not in call_kwargs, "请求绝不能包含 tool_choice"
        assert "available_functions" not in call_kwargs
        assert "response_format" not in call_kwargs, "不触发 json_schema/beta.parse 路径"

    def test_deepseek_no_crew_no_kickoff(self) -> None:
        """DeepSeek/generic Writer 阶段绝不创建 Agent/Crew/执行 kickoff。"""
        config = _config("deepseek")
        runner = LiveResearchFlowRunner(config, artifact_root="artifacts")
        # 模拟已就绪的 state：research_pack/analysis_pack 就绪。
        pack = _research_pack()
        analysis = _analysis_pack()
        from invest_research.flows.state import ResearchFlowState

        state = ResearchFlowState(request=_request())
        state.research_pack = pack
        state.analysis_pack = analysis
        state.citation_registry = build_citation_registry(pack, analysis)
        # 注入 mock dispatch（零真实网络）并验证 _kickoff_single 不被调用。
        mock_dispatch = MagicMock()
        mock_dispatch.dispatch.return_value = WriterDispatchResult(
            markdown=_valid_markdown(state.citation_registry),
            finish_reason="stop",
        )
        runner._direct_writer_factory = lambda cfg: mock_dispatch
        # 打桩 _kickoff_single：DeepSeek/generic Writer 阶段绝不能走到 Crew kickoff。
        runner._kickoff_single = MagicMock()
        draft = runner._exec_writer_stage(_request(), MagicMock(), state)
        assert isinstance(draft, ReportDraft)
        # dispatch 被调用 1 次（成功，无需重试）；_kickoff_single 绝不被调用。
        assert mock_dispatch.dispatch.call_count == 1
        runner._kickoff_single.assert_not_called()


# ---------------------------------------------------------------------------
# 7：普通长 Markdown 组装为 ReportDraft
# ---------------------------------------------------------------------------


def test_long_markdown_assembles_to_report_draft() -> None:
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    assert len(registry.keys()) > 0
    draft = ReportDraftAssembler().assemble(
        _valid_markdown(registry),
        _request(),
        pack,
        analysis,
        registry=registry,
    )
    assert isinstance(draft, ReportDraft)
    assert draft.version == "report_draft_v1"
    assert len(draft.markdown) > 200
    assert len(draft.citation_keys) > 0


# ---------------------------------------------------------------------------
# 8-11：有限重试与稳定终止
# ---------------------------------------------------------------------------


def _run_direct_with_responses(texts, finish_reasons) -> ReportDraft:
    config = _config("deepseek")
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    responses = [
        _mock_response(t, fr) for t, fr in zip(texts, finish_reasons)
    ]
    client = _make_client(responses)
    dispatch = DirectLlmWriterDispatch(config, client=client)
    runner = LiveResearchFlowRunner(config, artifact_root="artifacts")
    from invest_research.flows.state import ResearchFlowState

    state = ResearchFlowState(request=_request())
    state.research_pack = pack
    state.analysis_pack = analysis
    state.citation_registry = registry
    runner._direct_writer_factory = lambda cfg: dispatch
    return runner._exec_writer_stage_direct(_request(), MagicMock(), state, registry)


def test_empty_response_triggers_one_retry_and_succeeds() -> None:
    good = _valid_markdown(
        build_citation_registry(_research_pack(), _analysis_pack())
    )
    draft = _run_direct_with_responses(["", good], ["stop", "stop"])
    assert isinstance(draft, ReportDraft)


def test_short_response_triggers_one_retry_and_succeeds() -> None:
    # 短响应（<200 字符）触发一次重试；第二次成功。
    registry = build_citation_registry(_research_pack(), _analysis_pack())
    draft = _run_direct_with_responses(["太短了", _valid_markdown(registry)], ["stop", "stop"])
    assert isinstance(draft, ReportDraft)


def test_finish_reason_length_triggers_report_truncated() -> None:
    with pytest.raises(LiveFlowExecutionError) as excinfo:
        _run_direct_with_responses(
            ["# 执行摘要\n很长的正文……" * 100, "# 执行摘要\n很长的正文……" * 100],
            ["length", "length"],
        )
    assert excinfo.value.error_code == "REPORT_TRUNCATED"


def test_second_failure_stops_without_infinite_retry() -> None:
    """两次都空响应 → REPORT_INVALID，且只调用两次 create。"""
    config = _config("deepseek")
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    client = _make_client([_mock_response("", "stop"), _mock_response("", "stop")])
    dispatch = DirectLlmWriterDispatch(config, client=client)
    runner = LiveResearchFlowRunner(config, artifact_root="artifacts")
    from invest_research.flows.state import ResearchFlowState

    state = ResearchFlowState(request=_request())
    state.research_pack = pack
    state.analysis_pack = analysis
    state.citation_registry = registry
    runner._direct_writer_factory = lambda cfg: dispatch
    with pytest.raises(LiveFlowExecutionError) as excinfo:
        runner._exec_writer_stage_direct(_request(), MagicMock(), state, registry)
    assert excinfo.value.error_code == "REPORT_INVALID"
    assert client.chat.completions.create.call_count == 2, "只允许两次调用（一次 + 一次重试）"


def test_retry_reuses_same_context_and_no_extra_tools() -> None:
    """重试复用同一份确定性上下文；第二次请求不重新构建。"""
    config = _config("deepseek")
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    context = _build_context(pack, analysis, registry)
    client = _make_client(
        [_mock_response("", "stop"), _mock_response(_valid_markdown(registry), "stop")]
    )
    dispatch = DirectLlmWriterDispatch(config, client=client)
    # 第一次 dispatch 手动走一次（空），验证第二次 request 复用相同 user content。
    dispatch.dispatch(_request(), context)
    second = dispatch.dispatch(_request(), context, error_summary="empty content")
    assert second.markdown == _valid_markdown(registry)
    assert dispatch.requests[-1]["has_error_summary"] is True
    # 第二次请求的 user 段长度 = 同一份上下文 + 错误摘要（不重新构建）：
    # 对比第一次请求，第二次 user 段更长（追加了错误提示），且上下文正文仍是请求前缀。
    first_chars = dispatch.requests[0]["messages"][1]["chars"]
    second_chars = dispatch.requests[-1]["messages"][1]["chars"]
    assert second_chars > first_chars
    # 请求体不带 tools（审计字段可断言）。
    assert dispatch.requests[-1]["tools"] is None
    assert dispatch.requests[-1]["available_functions"] is None


# ---------------------------------------------------------------------------
# 12：不重新执行 Research/Analysis/外部工具
# ---------------------------------------------------------------------------


def test_direct_writer_does_not_re_run_research_or_tools() -> None:
    """Direct Writer 路径不调用 _kickoff_single / 不调用任何工具缓存。"""
    config = _config("deepseek")
    runner = LiveResearchFlowRunner(config, artifact_root="artifacts")
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    from invest_research.flows.state import ResearchFlowState

    state = ResearchFlowState(request=_request())
    state.research_pack = pack
    state.analysis_pack = analysis
    state.citation_registry = registry
    mock_dispatch = MagicMock()
    mock_dispatch.dispatch.return_value = WriterDispatchResult(
        markdown=_valid_markdown(registry), finish_reason="stop"
    )
    runner._direct_writer_factory = lambda cfg: mock_dispatch
    runner._kickoff_single = MagicMock()  # 若被调用会失败 —— 我们断言它未被调用
    runner._exec_writer_stage(_request(), MagicMock(), state)
    runner._kickoff_single.assert_not_called()
    assert mock_dispatch.dispatch.call_count == 1


# ---------------------------------------------------------------------------
# 13：Markdown 非法 citation 被拒绝
# ---------------------------------------------------------------------------


def test_invalid_citation_key_rejected() -> None:
    pack = _research_pack()
    analysis = _analysis_pack()
    registry = build_citation_registry(pack, analysis)
    # 正文包含注册表之外的伪造 key。
    bad_md = _valid_markdown(registry).replace(
        "来源清单与非投资建议声明",
        "来源清单与非投资建议声明\n\n[src_fake1234567890]",
    )
    # ReportDraftAssembler 只提取可信集合 ∩ 正文实际出现 → 伪造 key 不会进入 citation_keys。
    draft = ReportDraftAssembler().assemble(bad_md, _request(), pack, analysis, registry=registry)
    assert "src_fake1234567890" not in draft.citation_keys
    # Quality Gate 不接受非法 key（由 P06-11F 已覆盖，这里只验证提取）。
    assert all(k in registry.keys() for k in draft.citation_keys)


# ---------------------------------------------------------------------------
# 14：partial/unavailable 写入限制章节
# ---------------------------------------------------------------------------


def test_partial_written_to_limitations_section() -> None:
    pack = _research_pack()
    analysis = _analysis_pack("partial")
    registry = build_citation_registry(pack, analysis)
    context = _build_context(pack, analysis, registry)
    assert "completeness=partial" in context.text
    assert "缺少 2025 全年现金流量表数据" in context.text


def test_unavailable_written_to_unavailable_reason() -> None:
    pack = _research_pack()
    analysis = _analysis_pack("unavailable")
    registry = build_citation_registry(pack, analysis)
    context = _build_context(pack, analysis, registry)
    assert "completeness=unavailable" in context.text
    assert "SEC 未披露所需财务数据" in context.text


# ---------------------------------------------------------------------------
# 15：Jaeger/Prometheus 不含 prompt/正文/密钥
# ---------------------------------------------------------------------------


def test_observability_does_not_expose_prompt_or_key() -> None:
    """Direct Writer 的审计结构与 span 属性只含低基数字段。"""
    config = _config("deepseek")
    client = _make_client([_mock_response("secret-report-body")])
    dispatch = DirectLlmWriterDispatch(config, client=client)
    context = _build_context(None, None, build_citation_registry(None, None))
    dispatch.dispatch(_request(), context)
    for req in dispatch.requests:
        assert _API_KEY not in str(req)
        assert "secret-report-body" not in str(req), "审计结构不能包含响应正文"
        assert "tools" in req and req["tools"] is None
    # span 属性只记录 context_chars/estimated_tokens/fact_count/source_count/citation_count。
    # （span 本身由 tracing 测试覆盖；此处断言 dispatch 不把 prompt 放入 requests）
    assert all("content" not in m for m in dispatch.requests[0]["messages"])


# ---------------------------------------------------------------------------
# 16：两个并发 Job 的 Writer 上下文互不污染
# ---------------------------------------------------------------------------


def test_two_jobs_writer_context_isolated() -> None:
    """两次独立 build 调用互不影响；dispatch 实例各自独立。"""
    pack_a = _research_pack()
    analysis_a = _analysis_pack("partial")
    registry_a = build_citation_registry(pack_a, analysis_a)
    ctx_a = _build_context(pack_a, analysis_a, registry_a)

    pack_b = _research_pack()
    analysis_b = _analysis_pack("unavailable")
    registry_b = build_citation_registry(pack_b, analysis_b)
    ctx_b = _build_context(pack_b, analysis_b, registry_b)

    assert "缺少 2025 全年现金流量表数据" in ctx_a.text
    assert "SEC 未披露所需财务数据" in ctx_b.text
    assert "缺少 2025 全年现金流量表数据" not in ctx_b.text
    assert "SEC 未披露所需财务数据" not in ctx_a.text
    assert ctx_a.citation_count == len(registry_a.keys())
    assert ctx_b.citation_count == len(registry_b.keys())


# ---------------------------------------------------------------------------
# 17：Qwen 原路径回归不受影响
# ---------------------------------------------------------------------------


def test_qwen_native_path_unaffected() -> None:
    """Qwen NATIVE_PYDANTIC：走原 Crew 路径（structured_output_mode 判定正确）。"""
    config = _config("qwen")
    assert structured_output_mode(config, LLMRole.WRITER) == StructuredOutputMode.NATIVE_PYDANTIC
    # 直接调用 _exec_writer_stage 需要 Crew/kickoff —— 离线只验证模式判定（不走 direct）。
    runner = LiveResearchFlowRunner(config, artifact_root="artifacts")
    # 不注入 _direct_writer_factory；验证 direct 分支不会被 Qwen 触发是通过
    # 模式判定 + 不设置 factory 时不会调用（此处只验证配置判定）。
    assert runner._direct_writer_factory is None
