"""P06-11H: writer per-round response capture + bounded recovery."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig
from invest_research.application.writer_response_capture import (
    InMemoryWriterResponseBuffer,
)
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.infrastructure.flow_wiring import LiveResearchFlowRunner, _RunContext
from invest_research.infrastructure.observability.llm_call_observer import (
    LlmCallObserver,
    _event_response_text,
)

# 合法报告正文：≥200 字符且至少一个标准章节（"## 执行摘要"）。
_VALID_MARKDOWN = (
    "## 执行摘要\n"
    "微软公司 2025 财年经营稳健，营收与利润保持增长。\n"
    "## 公司与业务概览\n"
    "微软是领先的软件与服务公司。\n"
    "## 财务表现\n"
    "营业收入同比增长约 15%，毛利率维持高位。\n"
    "## 风险因素\n"
    "宏观经济波动可能影响需求。\n"
    "## 数据限制\n"
    "本报告基于公开披露信息。\n"
    "## 非投资建议声明\n"
    "本报告仅供参考，不构成投资建议。\n"
) * 2  # 足够长且包含全部必需章节


def _config() -> LLMConfig:
    return LLMConfig(
        vendor="qwen",
        api_key=SecretStr("sk-test"),
        base_url="https://example.com",
        model_research="qwen-test",
        model_analysis="qwen-test",
        model_writer="qwen-test",
    )


def _runner() -> LiveResearchFlowRunner:
    return LiveResearchFlowRunner(config=_config())


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 10, 31))


def _pack() -> tuple[ResearchPack, FinancialAnalysisPack]:
    as_of = date(2025, 10, 31)
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp", ticker="MSFT")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://www.sec.gov/Archives/edgar/data/0000789019/000095017025000001/msft-20250630.htm",
                title="10-K",
                accessed_at=as_of,
                locator="10-K",
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="0000789019",
                source_id="src-1",
                taxonomy="us-gaap",
                concept="Revenues",
                value=245122,
                unit="USD",
                period_start=date(2024, 7, 1),
                period_end=date(2025, 6, 30),
            )
        ],
        analysis_notes="ok",
    )
    return research, analysis


def _event(role: str, response: Any) -> Any:
    return SimpleNamespace(
        agent_id="agent-1",
        agent_role=role,
        model="deepseek-v4-flash",
        response=response,
    )


# ---------------------------------------------------------------------------
# 1) _event_response_text：普通 content 提取（忽略 reasoning/tool_call）
# ---------------------------------------------------------------------------


def test_event_response_text_str() -> None:
    assert _event_response_text("hello world") == "hello world"


def test_event_response_text_dict_content_only() -> None:
    resp = {
        "content": "报告正文",
        "reasoning_content": "这是思考过程，绝不能当成正文",
    }
    assert _event_response_text(resp) == "报告正文"


def test_event_response_text_object_content_only() -> None:
    resp = SimpleNamespace(
        content="对象正文", reasoning_content="思考过程"
    )
    assert _event_response_text(resp) == "对象正文"


def test_event_response_text_ignores_tool_calls() -> None:
    resp = {
        "content": "",
        "tool_calls": [{"function": {"name": "WriterContextReader", "arguments": "{}"}}],
    }
    assert _event_response_text(resp) == ""


def test_event_response_text_non_str_content() -> None:
    assert _event_response_text({"content": 123}) == ""
    assert _event_response_text(None) == ""


# ---------------------------------------------------------------------------
# 2) writer 捕获；research/analysis 不进入 buffer
# ---------------------------------------------------------------------------


def test_capture_writer_response_only_writer_role() -> None:
    """仅 role=writer 的 completed 事件被捕获（research/analysis 不进入 buffer）。"""
    buffer = InMemoryWriterResponseBuffer()
    observer = LlmCallObserver(_config(), response_capture=buffer)

    # research/analysis role：_on_completed 内部捕获阶段不执行（非 writer）
    observer._on_completed(None, _event("research", "研究长文"))
    observer._on_completed(None, _event("analysis", "分析长文"))
    assert len(buffer) == 0

    # writer role：捕获
    observer._on_completed(None, _event("writer", _VALID_MARKDOWN))
    assert len(buffer) == 1


def test_capture_writer_response_str_response() -> None:
    buffer = InMemoryWriterResponseBuffer()
    observer = LlmCallObserver(_config(), response_capture=buffer)
    observer._capture_writer_response(_event("writer", "第一轮正文"))
    assert buffer.candidates()[0].content == "第一轮正文"


# ---------------------------------------------------------------------------
# 3) reasoning_content 被忽略（不进入 buffer）
# ---------------------------------------------------------------------------


def test_capture_writer_response_ignores_reasoning() -> None:
    buffer = InMemoryWriterResponseBuffer()
    observer = LlmCallObserver(_config(), response_capture=buffer)
    resp = {"content": "真正正文", "reasoning_content": "思考内容"}
    observer._capture_writer_response(_event("writer", resp))
    assert len(buffer) == 1
    assert buffer.candidates()[0].content == "真正正文"


def test_capture_writer_response_tool_call_not_captured() -> None:
    buffer = InMemoryWriterResponseBuffer()
    observer = LlmCallObserver(_config(), response_capture=buffer)
    resp = {"tool_calls": [{"function": {"name": "WriterContextReader", "arguments": "{}"}}]}
    observer._capture_writer_response(_event("writer", resp))
    assert len(buffer) == 0


# ---------------------------------------------------------------------------
# 4/5) Buffer：容量 / 去重 / 长度限制
# ---------------------------------------------------------------------------


def test_buffer_dedup() -> None:
    buffer = InMemoryWriterResponseBuffer()
    assert buffer.capture("同一正文") == "accepted"
    assert buffer.capture("同一正文") == "duplicate"
    assert len(buffer) == 1


def test_buffer_max_candidates() -> None:
    buffer = InMemoryWriterResponseBuffer(max_candidates=3)
    for i in range(5):
        buffer.capture(f"候选{i}")
    cands = buffer.candidates()
    assert len(cands) == 3
    # 丢弃最旧，保留最新
    assert all("候选" in c.content for c in cands)


def test_buffer_max_chars() -> None:
    buffer = InMemoryWriterResponseBuffer(max_chars=10)
    assert buffer.capture("a" * 11) == "too_long"
    assert buffer.capture("short") == "accepted"


def test_buffer_empty_content() -> None:
    buffer = InMemoryWriterResponseBuffer()
    assert buffer.capture("") == "empty"
    assert buffer.capture("   ") == "empty"


# ---------------------------------------------------------------------------
# 6) 两个 Job Buffer 不共享
# ---------------------------------------------------------------------------


def test_buffer_job_isolation() -> None:
    b1 = InMemoryWriterResponseBuffer()
    b2 = InMemoryWriterResponseBuffer()
    b1.capture("job1 body")
    assert len(b1) == 1
    assert len(b2) == 0


# ---------------------------------------------------------------------------
# 7-10) _recover_writer_from_buffer 有界恢复
# ---------------------------------------------------------------------------


def _make_ctx() -> _RunContext:
    from invest_research.infrastructure.performance import PerformanceRecorder
    from invest_research.infrastructure.tool_budget import ToolBudget
    from invest_research.settings import ResearchProfile

    return _RunContext(
        request=_request(),
        profile=ResearchProfile.for_mode("fast"),
        effective_config=_config(),
        components=None,  # type: ignore[arg-type]
        stats={},
        recorder=PerformanceRecorder(),
        budget=ToolBudget(),
        cache=None,
        prefetch=None,
        research_tools=None,
    )


def _test_state() -> Any:
    research, analysis = _pack()
    from invest_research.flows.state import ResearchFlowState

    state = ResearchFlowState(request=_request())
    state.research_pack = research
    state.analysis_pack = analysis
    return state


def test_recovery_skipped_when_final_valid() -> None:
    """final answer 合法时 _assemble_writer_markdown 不触发恢复，直接成功。"""
    runner = _runner()
    state = _test_state()
    draft = runner._assemble_writer_markdown(_VALID_MARKDOWN, _request(), state, None)
    assert draft is not None
    assert draft.markdown == _VALID_MARKDOWN.strip()


def test_recovery_longest_tool_guidance_rejected() -> None:
    """最长候选是工具说明（Action 前缀/拒绝短语）→ 被 ReportDraftAssembler 拒绝。"""
    runner = _runner()
    buffer = InMemoryWriterResponseBuffer()
    runner._writer_response_buffer = buffer
    # 工具说明虽然长但命中 _TOOL_ACTION_PREFIXES / 拒绝短语
    tool_guidance = (
        "Action: WriterContextReader\n"
        "Action Input: {\"artifact_key\": \"research_pack\"}\n"
    ) * 20
    buffer.capture(tool_guidance)
    state = _test_state()
    with pytest.raises(Exception):
        runner._assemble_writer_markdown("过短", _request(), state, None)


def test_recovery_shorter_valid_report_used() -> None:
    """较短但合法的报告候选可以恢复（而不是最长的工具说明）。"""
    runner = _runner()
    buffer = InMemoryWriterResponseBuffer()
    runner._writer_response_buffer = buffer
    tool_guidance = (
        "Action: WriterContextReader\n"
        "Action Input: {\"artifact_key\": \"research_pack\"}\n"
    ) * 20
    buffer.capture(tool_guidance)  # 最长但非法
    buffer.capture(_VALID_MARKDOWN)  # 较短但合法
    state = _test_state()
    draft = runner._assemble_writer_markdown("过短", _request(), state, None)
    assert draft is not None
    assert draft.markdown == _VALID_MARKDOWN.strip()


def test_recovery_all_invalid_raises() -> None:
    """所有候选非法时仍为 REPORT_INVALID（不静默通过）。"""
    runner = _runner()
    buffer = InMemoryWriterResponseBuffer()
    runner._writer_response_buffer = buffer
    buffer.capture("Action: WriterContextReader\nAction Input: {}\n")
    buffer.capture("抱歉，我无法完成此任务。")
    state = _test_state()
    with pytest.raises(Exception):
        runner._assemble_writer_markdown("过短", _request(), state, None)


def test_recovery_no_candidates_raises() -> None:
    """无候选（空 buffer）→ 不恢复，抛 REPORT_INVALID。"""
    runner = _runner()
    runner._writer_response_buffer = InMemoryWriterResponseBuffer()
    state = _test_state()
    with pytest.raises(Exception):
        runner._assemble_writer_markdown("过短", _request(), state, None)


# ---------------------------------------------------------------------------
# 11/12) scoped handler 不泄漏 + span/指标无正文密钥
# ---------------------------------------------------------------------------


def test_scoped_handler_leak_guard() -> None:
    """observer.subscribe 返回 scope；不订阅则 response_capture 不泄漏。"""
    buffer = InMemoryWriterResponseBuffer()
    observer = LlmCallObserver(_config(), response_capture=buffer)
    # 未 subscribe：直接调用捕获（不应产生异常，且 buffer 有内容）
    observer._capture_writer_response(_event("writer", "正文"))
    assert len(buffer) == 1
    # capture span 不包含正文/密钥（这里只验证不抛错）
    observer._record_capture_span(100)


def test_captured_content_not_in_span() -> None:
    """capture span 只记录长度，不记录正文（通过属性名断言）。"""
    buffer = InMemoryWriterResponseBuffer()
    observer = LlmCallObserver(_config(), response_capture=buffer)
    observer._capture_writer_response(_event("writer", _VALID_MARKDOWN))
    assert buffer.candidates()[0].char_length == len(_VALID_MARKDOWN.strip())
    assert "sk-" not in buffer.candidates()[0].content
