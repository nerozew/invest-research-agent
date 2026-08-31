"""P06-11N：指标进入报告 + Direct Writer 统一观测的离线回归测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY
from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig
from invest_research.application.citation_registry import build_citation_registry
from invest_research.application.report_draft_assembler import (
    ReportAssemblerError,
    ReportDraftAssembler,
)
from invest_research.application.writer_context_builder import WriterContextBuilder
from invest_research.application.writer_direct_dispatch import WriterDispatchResult
from invest_research.domain.models import (
    AnalysisCompleteness,
    CompanyIdentity,
    FinancialAnalysisPack,
    MetricResult,
    MetricStatus,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.infrastructure.direct_writer_dispatch import DirectLlmWriterDispatch
from invest_research.infrastructure.flow_wiring import _record_direct_metrics

_METRIC_NAMES = (
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "net_income_growth",
    "current_ratio",
    "asset_liability_ratio",
    "operating_cash_flow_ratio",
    "free_cash_flow",
    "roa",
)


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 10, 31))


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=CompanyIdentity(
            cik="0000789019", ticker="MSFT", legal_name="Microsoft Corporation"
        ),
        as_of_date=date(2025, 10, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://www.sec.gov/Archives/msft-10k.htm",
                title="MSFT 10-K",
                accessed_at=date(2025, 10, 31),
            )
        ],
    )


def _analysis_pack() -> FinancialAnalysisPack:
    metrics = [
        MetricResult(
            job_id="job-p06-11n",
            metric_name=name,
            period_end=date(2025, 6, 30),
            value=Decimal(index) / Decimal("100"),
            unit="ratio" if name != "free_cash_flow" else "USD",
            status=MetricStatus.COMPUTED,
            formula_version="financial_metrics_v1",
            inputs_json={"source_facts": [{"concept": "Revenues"}]},
        )
        for index, name in enumerate(_METRIC_NAMES, start=1)
    ]
    return FinancialAnalysisPack(
        version="analysis_pack_v2",
        schema_version="analysis_pack_v2",
        period_end=date(2025, 6, 30),
        metrics=metrics,
        completeness=AnalysisCompleteness.COMPLETE,
    )


def _config() -> LLMConfig:
    return LLMConfig(
        vendor="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key=SecretStr("test-key-not-real"),
        model_research="p06-11n-research",
        model_analysis="p06-11n-analysis",
        model_writer="p06-11n-writer",
    )


def _complete_markdown(*, include_metrics: bool) -> str:
    metric_text = "\n".join(
        f"| `{name}` | computed | 0.1 | ratio |" for name in _METRIC_NAMES
    )
    if not include_metrics:
        metric_text = "| 收入增长 | 10% |"
    return (
        "## 执行摘要\n公司经营信息概览，本段用于保证报告正文达到合法长度并清楚说明数据边界。\n\n"
        "## 公司与业务概览\n公司业务来自可信公开资料，本文不添加上游数据之外的新事实。\n\n"
        "## 财务表现\n### 关键指标表\n| 指标代码 | 状态 | 数值 | 单位 |\n"
        "|---|---|---:|---|\n"
        f"{metric_text}\n\n"
        "## 风险因素与催化因素\n行业竞争和宏观环境变化可能影响未来表现。\n\n"
        "## 数据限制\n仅使用数据截止日前可获得的公开数据。\n\n"
        "## 来源清单与非投资建议声明\n本报告仅供研究，不构成投资建议。"
    )


def _sample(name: str, labels: dict[str, str]) -> float:
    return float(REGISTRY.get_sample_value(name, labels) or 0.0)


def test_writer_context_contains_all_ten_deterministic_metrics() -> None:
    research, analysis = _research_pack(), _analysis_pack()
    registry = build_citation_registry(research, analysis)
    context = WriterContextBuilder().build(_request(), research, analysis, registry)

    assert context.metric_count == 10
    for name in _METRIC_NAMES:
        assert f"code=`{name}`" in context.text


def test_report_assembler_rejects_missing_metric_coverage_and_accepts_complete() -> None:
    research, analysis = _research_pack(), _analysis_pack()
    registry = build_citation_registry(research, analysis)

    with pytest.raises(ReportAssemblerError, match="核心指标代码") as caught:
        ReportDraftAssembler().assemble(
            _complete_markdown(include_metrics=False),
            _request(),
            research,
            analysis,
            registry=registry,
            require_metric_coverage=True,
        )
    assert caught.value.error_code == "REPORT_METRICS_MISSING"

    draft = ReportDraftAssembler().assemble(
        _complete_markdown(include_metrics=True),
        _request(),
        research,
        analysis,
        registry=registry,
        require_metric_coverage=True,
    )
    assert all(name in draft.markdown for name in _METRIC_NAMES)


def test_direct_dispatch_extracts_cached_input_tokens() -> None:
    research, analysis = _research_pack(), _analysis_pack()
    context = WriterContextBuilder().build(
        _request(), research, analysis, build_citation_registry(research, analysis)
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=_complete_markdown(include_metrics=True)),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=123,
            completion_tokens=45,
            prompt_tokens_details={"cached_tokens": 67},
        ),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = response

    result = DirectLlmWriterDispatch(_config(), client=client).dispatch(_request(), context)
    assert (result.input_tokens, result.output_tokens, result.cached_input_tokens) == (
        123,
        45,
        67,
    )


def test_direct_writer_updates_unified_llm_request_and_token_metrics() -> None:
    labels = {
        "provider": "deepseek",
        "model": "p06-11n-writer",
        "role": "writer",
    }
    request_before = _sample("llm_requests_total", {**labels, "status": "success"})
    token_before = {
        kind: _sample("llm_tokens_total", {**labels, "type": kind})
        for kind in ("input", "output", "cached_input")
    }

    _record_direct_metrics(
        WriterDispatchResult(
            markdown=_complete_markdown(include_metrics=True),
            input_tokens=123,
            output_tokens=45,
            cached_input_tokens=67,
            duration_s=1.25,
        ),
        "success",
        _config(),
    )

    assert _sample("llm_requests_total", {**labels, "status": "success"}) - request_before == 1
    assert _sample("llm_tokens_total", {**labels, "type": "input"}) - token_before["input"] == 123
    assert _sample("llm_tokens_total", {**labels, "type": "output"}) - token_before["output"] == 45
    assert (
        _sample("llm_tokens_total", {**labels, "type": "cached_input"})
        - token_before["cached_input"]
        == 67
    )
