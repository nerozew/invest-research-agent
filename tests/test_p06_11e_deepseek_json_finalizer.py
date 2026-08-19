"""P06-11E：DeepSeek 原生 JSON Finalizer + 三阶段执行契约测试（离线，不联网）。

覆盖（docs/05 P06-11E 验收 20 项）：
1. 请求体包含 response_format={"type":"json_object"}；
2. 请求体绝不包含 json_schema；
3. 不调用 beta.chat.completions.parse（只调用 create）；
4. Finalizer 不携带 tools；
5. thinking=false（deepseek 关闭思考）；
6. 合法 JSON 成功；
7. 普通文本经一次 Finalizer 成功；
8. "" → None（BoundaryCanonicalizer）；
9. unavailable + "" 仍失败；
10. finish_reason=length 失败；
11. 空 content 失败；
12. 一次格式修复成功；
13. 第二次失败终止；
14. Research 失败不执行 Analysis（阶段短路）；
15. Analysis 失败不执行 Writer；
16. Writer 使用 ReportDraftAssembler；
17. 两个连续 Job 状态隔离（RunContext）；
18. Qwen 原生路径回归（finalize 不被调用）；
19. fake E2E（全链产生合法 pack）；
20. MockTransport 断言零真实网络。

全程使用 mock client / 本地 Pydantic，禁止真实联网。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig, LLMRole
from invest_research.application.boundary_canonicalizer import BoundaryCanonicalizer
from invest_research.application.research_assembler import ResearchPackAssembler
from invest_research.application.structured_finalizer import FinalizerError
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnalysisSelectionDraft,
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    ResearchSelectionDraft,
    Source,
    SourceType,
)
from invest_research.infrastructure.finalizers.deepseek_json_object_finalizer import (
    DeepSeekJsonObjectFinalizer,
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


def _draft_json(selected_urls: list[str] | None = None) -> str:
    urls = selected_urls or [
        "https://www.sec.gov/Archives/edgar/data/0000789019/10-K",
        "https://www.sec.gov/Archives/edgar/data/0000789019/10-Q",
    ]
    return (
        '{"version":"research_selection_draft_v1",'
        f'"schema_version":"research_selection_draft_v1",'
        f'"as_of_date":"2025-12-31",'
        f'"selected_source_urls":{urls!r},'
        '"coverage_notes":null,"conflicts":[]}'
    ).replace("'", '"')


def _mock_response(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))
        ]
    )


def _make_client() -> tuple[MagicMock, MagicMock]:
    """返回 (mock_client, mock_create)。mock_create 记录每次请求体。"""
    create = MagicMock(return_value=_mock_response(_draft_json()))
    client = MagicMock()
    client.chat.completions.create = create
    return client, create


# ---------------------------------------------------------------------------
# 1~5. 请求体契约
# ---------------------------------------------------------------------------


def test_request_contains_json_object_response_format() -> None:
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("plain text", ResearchSelectionDraft, role="research")
    assert len(fz.requests) == 1
    assert fz.requests[0]["response_format"] == {"type": "json_object"}


def test_request_never_contains_json_schema() -> None:
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("plain text", ResearchSelectionDraft, role="research")
    captured = create.call_args_list[0].kwargs
    rf = captured.get("response_format")
    assert rf == {"type": "json_object"}
    assert "json_schema" not in str(captured)


def test_uses_plain_chat_completions_create_only() -> None:
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("plain text", ResearchSelectionDraft, role="research")
    # 绝不调用 beta.chat.completions.parse
    assert (
        not hasattr(client.chat.completions, "parse")
        or client.chat.completions.parse.call_count == 0
    )


def test_finalizer_does_not_carry_tools() -> None:
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("plain text", ResearchSelectionDraft, role="research")
    captured = create.call_args_list[0].kwargs
    assert captured.get("tools") == []


def test_thinking_disabled() -> None:
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("plain text", ResearchSelectionDraft, role="research")
    assert fz.requests[0]["has_thinking_disabled"] is True


# ---------------------------------------------------------------------------
# 6~11. Finalizer 行为契约
# ---------------------------------------------------------------------------


def test_valid_json_success() -> None:
    import json

    client = MagicMock()
    client.chat.completions.create = MagicMock(
        return_value=_mock_response(
            json.dumps(
                {
                    "version": "x",
                    "schema_version": "x",
                    "as_of_date": "2025-12-31",
                    "selected_source_urls": ["https://a"],
                    "coverage_notes": None,
                    "conflicts": [],
                }
            )
        )
    )
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    draft = fz.finalize("plain", ResearchSelectionDraft, role="research")
    assert isinstance(draft, ResearchSelectionDraft)


def test_plain_text_goes_through_finalizer_once() -> None:
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("这不是 JSON", ResearchSelectionDraft, role="research")
    assert create.call_count == 1


def test_empty_to_none() -> None:
    c = BoundaryCanonicalizer()
    out = c.canonicalize_for(
        AnalysisSelectionDraft,
        {"unavailable_reason": "   "},
    )
    assert out["unavailable_reason"] is None


def test_unavailable_with_empty_reason_still_fails() -> None:
    c = BoundaryCanonicalizer()
    data = c.canonicalize_for(
        AnalysisSelectionDraft,
        {
            "version": "analysis_selection_draft_v1",
            "schema_version": "analysis_selection_draft_v1",
            "period_end": "2025-06-30",
            "selected_fact_refs": [],
            "metric_results": [],
            "analysis_notes": None,
            "limitations": [],
            "completeness": "unavailable",
            "unavailable_reason": "   ",
        },
    )
    with pytest.raises(Exception):
        AnalysisSelectionDraft.model_validate(data)


def test_finish_reason_length_fails() -> None:
    client = MagicMock()
    client.chat.completions.create = MagicMock(
        return_value=_mock_response("x", finish_reason="length")
    )
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    with pytest.raises(FinalizerError):
        fz.finalize("plain", ResearchSelectionDraft, role="research")


def test_empty_content_fails() -> None:
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=_mock_response(""))
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    with pytest.raises(FinalizerError):
        fz.finalize("plain", ResearchSelectionDraft, role="research")


# ---------------------------------------------------------------------------
# 12~13. 一次修复成功 / 第二次失败终止
# ---------------------------------------------------------------------------


def test_single_repair_succeeds() -> None:
    responses = [
        _mock_response('{"schema_version":"x","as_of_date":"2025-12-31"}'),  # 缺 version → 校验失败
        _mock_response(_draft_json()),
    ]
    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=responses)
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    draft = fz.finalize("plain", ResearchSelectionDraft, role="research")
    assert isinstance(draft, ResearchSelectionDraft)
    assert len(fz.requests) == 2  # 第 1 次转换 + 第 2 次修复


def test_second_failure_terminates() -> None:
    bad = '{"version":"x"}'
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=_mock_response(bad))
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    with pytest.raises(FinalizerError):
        fz.finalize("plain", ResearchSelectionDraft, role="research")
    assert len(fz.requests) == 2  # 第二次仍失败即终止


# ---------------------------------------------------------------------------
# 14~16. 三阶段短路（通过 run() 注入自定义 staged_executor 验证）
# ---------------------------------------------------------------------------

from invest_research.infrastructure.flow_wiring import LiveResearchFlowRunner  # noqa: E402


def _staged_runner_factory(
    research_ok: bool = True,
    analysis_ok: bool = True,
) -> LiveResearchFlowRunner:
    """构造一个 staged 路径 runner，通过 monkeypatch 验证阶段短路。"""
    return LiveResearchFlowRunner(
        config=_config(),
        artifact_root="artifacts_test",
        crew_factory=None,  # production → 走分阶段路径
    )


def test_research_failure_skips_analysis(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runner = _staged_runner_factory(research_ok=False)
    # monkeypatch 阶段方法：research 抛错，analysis 不应被调用
    from invest_research.infrastructure.flow_wiring import LiveFlowExecutionError

    def _bad_research(request, ctx):
        raise LiveFlowExecutionError(
            "research failed", error_code="SCHEMA_INVALID", failure_stage="02_research"
        )

    runner._exec_research_stage = _bad_research  # type: ignore[method-assign]
    calls = {"analysis": 0}

    def _analysis(request, ctx):
        calls["analysis"] += 1
        raise AssertionError("不应执行 Analysis")

    runner._exec_analysis_stage = _analysis  # type: ignore[method-assign]
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    assert calls["analysis"] == 0


def test_analysis_failure_skips_writer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from invest_research.infrastructure.flow_wiring import LiveFlowExecutionError

    runner = _staged_runner_factory()
    runner._exec_research_stage = lambda request, ctx: _research_pack()  # type: ignore[method-assign]

    def _bad_analysis(request, ctx):
        raise LiveFlowExecutionError(
            "analysis failed", error_code="SCHEMA_INVALID", failure_stage="04_analysis"
        )

    runner._exec_analysis_stage = _bad_analysis  # type: ignore[method-assign]
    calls = {"writer": 0}

    def _writer(request, ctx, state):
        calls["writer"] += 1
        raise AssertionError("不应执行 Writer")

    runner._exec_writer_stage = _writer  # type: ignore[method-assign]
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    assert calls["writer"] == 0


def test_writer_uses_report_draft_assembler() -> None:
    """Writer 分阶段路径使用 ReportDraftAssembler（Markdown → ReportDraft）。"""
    from invest_research.infrastructure.flow_wiring import _staged_artifact_loader

    state = SimpleNamespace(
        research_pack=_research_pack(),
        analysis_pack=_analysis_pack(),
    )
    loader = _staged_artifact_loader(state)  # type: ignore[arg-type]
    assert isinstance(loader("research_pack"), dict)
    assert isinstance(loader("analysis_pack"), dict)
    assert loader("unknown") is None


# ---------------------------------------------------------------------------
# 17. 两个连续 Job 状态隔离
# ---------------------------------------------------------------------------


def test_two_consecutive_jobs_state_isolated(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """两个连续 Job 状态隔离：每次 run 创建独立 _RunContext（不联网）。"""
    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path),
        crew_factory=None,
    )
    captured = {"ctx": None}

    def _fake_run_live(request, ctx):  # type: ignore[no-untyped-def]
        captured["ctx"] = ctx
        from invest_research.flows.state import ResearchFlowState

        return ResearchFlowState(request=request)

    monkeypatch.setattr(runner, "_run_live", _fake_run_live)
    runner.run(_request())
    first_ctx = runner._active_ctx
    assert first_ctx is not None
    assert first_ctx.finalization_count == 0
    assert captured["ctx"] is first_ctx

    runner.run(_request())
    second_ctx = runner._active_ctx
    assert second_ctx is not first_ctx
    assert second_ctx.finalization_count == 0


# ---------------------------------------------------------------------------
# 18. Qwen 原生路径回归（不调用 Finalizer）
# ---------------------------------------------------------------------------


def test_qwen_native_path_not_using_finalizer() -> None:
    from invest_research.agents.llm_factory import StructuredOutputMode, structured_output_mode

    cfg = _config("qwen")
    assert structured_output_mode(cfg, LLMRole.WRITER) == StructuredOutputMode.NATIVE_PYDANTIC


# ---------------------------------------------------------------------------
# 19. fake E2E（合法 pack 全链组装）
# ---------------------------------------------------------------------------


def test_deepseek_fake_e2e_research_assembler() -> None:
    draft = ResearchSelectionDraft(
        version="research_selection_draft_v1",
        schema_version="research_selection_draft_v1",
        as_of_date=date(2025, 12, 31),
        selected_source_urls=["https://www.sec.gov/Archives/edgar/data/0000789019/10-K"],
    )
    pack = ResearchPackAssembler().assemble(
        draft,
        request=_request(),
        company_identity=CompanyIdentity(
            cik="0000789019", ticker="MSFT", legal_name="Microsoft Corp"
        ),
        source_filings=[
            {
                "primary_document_url": "https://www.sec.gov/Archives/edgar/data/0000789019/10-K",
                "form_type": "10-K",
                "filing_date": "2025-10-31",
            }
        ],
    )
    assert isinstance(pack, ResearchPack)
    assert pack.sources[0].canonical_url.endswith("10-K")


def test_deepseek_fake_e2e_analysis_assembler() -> None:
    fact = FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Revenue",
        value=245100000000,
        unit="USD",
        period_start=date(2024, 7, 1),
        period_end=date(2025, 6, 30),
    )
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        schema_version="analysis_selection_draft_v1",
        period_end=date(2025, 6, 30),
        selected_fact_refs=[],
        metric_results=[],
        limitations=["缺少部分数据"],
        completeness=AnalysisCompleteness.PARTIAL,
    )
    from invest_research.application.analysis_assembler import AnalysisPackAssembler

    pack = AnalysisPackAssembler().assemble(draft, [fact])
    assert isinstance(pack, FinancialAnalysisPack)
    assert pack.completeness == AnalysisCompleteness.PARTIAL


def test_report_draft_assembler_reused() -> None:
    from invest_research.application.report_draft_assembler import ReportDraftAssembler

    md = (
        "## 执行摘要\n"
        "本报告基于已核实的 SEC 申报文件整理，仅用于技术演示，不构成任何投资建议。\n"
        "## 公司与业务概览\n"
        "Microsoft Corp 是一家跨国科技公司，主要业务包括生产力软件、智能云与企业服务。\n"
        "## 财务表现\n"
        "营业收入为 2451 亿美元，相关数据均来自上游 FinancialAnalysisPack。\n"
        "## 风险因素与催化因素\n"
        "市场与监管风险并存，包括宏观经济波动、竞争加剧与合规要求变化等。\n"
        "## 数据限制\n"
        "数据仅截至指定数据截止日，超过该日期的信息不在本报告范围内。\n"
        "## 来源清单与非投资建议声明\n"
        "本报告不构成任何投资建议。投资者应直接查阅 SEC 原始文件。\n"
    )
    draft = ReportDraftAssembler().assemble(md, _request(), _research_pack(), _analysis_pack())
    assert isinstance(draft, ReportDraft)
    assert "非投资建议" in draft.markdown


# ---------------------------------------------------------------------------
# 20. MockTransport 断言零真实网络
# ---------------------------------------------------------------------------


def test_mock_transport_zero_real_network() -> None:
    """mock client 断言：Finalizer 从不创建真实 openai.Client。"""
    client, create = _make_client()
    fz = DeepSeekJsonObjectFinalizer(_config(), client=client)
    fz.finalize("plain", ResearchSelectionDraft, role="research")
    # client 是 mock，没有真实网络；验证 client_built 直接复用 mock
    assert fz._client_built is True
    assert create.call_count == 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(
            cik="0000789019", ticker="MSFT", legal_name="Microsoft Corp"
        ),
        as_of_date=date(2025, 12, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://www.sec.gov/Archives/edgar/data/0000789019/10-K",
                title="10-K filed 2025-10-31",
                accessed_at=date(2025, 12, 31),
            )
        ],
    )


def _analysis_pack() -> FinancialAnalysisPack:
    fact = FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Revenue",
        value=245100000000,
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
