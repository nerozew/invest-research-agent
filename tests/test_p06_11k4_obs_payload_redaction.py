"""P06-11K-4 测试 17：Jaeger/Prometheus 不包含完整 Payload + 工具摘要脱敏。

验收点（任务文档第九节测试 17 + 工具摘要脱敏补充）：
1. Jaeger Span 只加 diagnostic.* 白名单低基数属性（event_id/available/payload_kind/
   input_size/output_size/validation_error_count），绝不塞完整 Payload；
2. LLM 摘要（content/tool_calls/empty 三态）只含计数/长度/角色，不包含回复正文；
3. 工具参数白名单摘要（SEC/Serper）经 redaction 后可查看——不暴露 api_key/
   authorization/cookie/URL 签名等敏感键；结果摘要只含来源 URL/accession/locator/
   标题/结果数量/有界预览；
4. Prometheus labels（通过 metrics 端点文本）不含 payload 正文。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from invest_research.application.diagnostics.redaction import redact_payload
from invest_research.application.diagnostics.tool_summaries import (
    build_llm_response_summary,
    build_tool_request_summary,
    build_tool_response_summary,
)
from invest_research.infrastructure.observability.llm_full_observer import LlmFullObserver

CFG = SimpleNamespace(vendor="deepseek", base_url=None, role_overrides={})


@pytest.fixture()
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    from opentelemetry import trace
    from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE  # noqa: PLC2701

    _TRACER_PROVIDER_SET_ONCE._done = False
    trace.set_tracer_provider(provider)
    return exporter


# ---------------------------------------------------------------------------
# 17a：Jaeger Span 只加 diagnostic.* 白名单属性（不塞完整 Payload）
# ---------------------------------------------------------------------------


def test_llm_span_attributes_only_diagnostic_whitelist(
    memory_exporter: InMemorySpanExporter,
) -> None:
    obs = LlmFullObserver(CFG, agent_roles={"a1": "research"})
    obs._on_llm_started(
        None, SimpleNamespace(type="start", model="m", agent_id="a1", agent_role="research")
    )
    obs._on_llm_completed(
        None,
        SimpleNamespace(
            type="completed",
            model="m",
            agent_id="a1",
            agent_role="research",
            response=SimpleNamespace(content="ok", usage=None),
            call_type="llm_call",
        ),
    )
    spans = [s for s in memory_exporter.get_finished_spans() if s.name == "llm.request"]
    assert len(spans) == 1
    attrs = spans[0].attributes or {}
    # 白名单属性必须存在。
    assert attrs.get("diagnostic.available") is True
    assert attrs.get("diagnostic.payload_kind") == "llm_response"
    assert attrs.get("diagnostic.validation_error_count") == 0
    assert str(attrs.get("diagnostic.event_id", "")).startswith("llm.research.")
    # 绝不允许出现非白名单列出的 dict payload / 完整正文类属性。
    non_whitelist = {
        k for k in attrs if k.startswith("diagnostic.") and k not in {
            "diagnostic.event_id",
            "diagnostic.available",
            "diagnostic.payload_kind",
            "diagnostic.input_size",
            "diagnostic.output_size",
            "diagnostic.validation_error_count",
        }
    }
    assert non_whitelist == set()
    # 任何属性值都不包含完整 payload 正文磁盘（纵深：不记录 content）。
    for value in attrs.values():
        assert "ok" not in str(value).split("|")[0]  # 只检查非白名单值不含长文


# ---------------------------------------------------------------------------
# 17b：LLM 三态摘要只含计数/长度，不含回复正文
# ---------------------------------------------------------------------------


def test_llm_summary_content_kind_no_body() -> None:
    summary = build_llm_response_summary(
        "content",
        role="writer",
        content_chars=123,
        finish_reason="stop",
        input_tokens=10,
        output_tokens=5,
    )
    assert summary["kind"] == "content"
    assert summary["content_chars"] == 123
    assert "content_text" not in summary
    assert "text" not in summary
    assert "markdown" not in summary


def test_llm_summary_empty_and_tool_calls_kinds() -> None:
    empty = build_llm_response_summary("empty", role="research")
    assert empty["kind"] == "empty"
    tool_calls = build_llm_response_summary(
        "tool_calls", role="analysis", tool_call_count=3
    )
    assert tool_calls["kind"] == "tool_calls"
    assert tool_calls["tool_call_count"] == 3


# ---------------------------------------------------------------------------
# 17c：工具参数白名单摘要在 redaction 后不泄露敏感键
# ---------------------------------------------------------------------------


def test_tool_request_summary_sanitizes_sensitive_params() -> None:
    raw_params = {
        "cik": "0000789019",
        "as_of_date": "2025-01-01",
        "requested_forms": "10-K,10-Q",
        "api_key": "sk-secret-key-123",
        "authorization": "Bearer sk-abc",
        "cookie": "session=abc",
        "url": "https://sec.gov/x?a=b&api_key=sk-force",
    }
    summary = build_tool_request_summary("sec_submissions", raw_params)
    # 白名单只保留 cik/as_of_date/requested_forms。
    assert set(summary.keys()) == {"cik", "as_of_date", "requested_forms"}
    # 纵深脱敏（即使构造器白名单外字段被意外带入，redaction 也会抹掉）。
    redacted = redact_payload(raw_params).data
    assert "sk-secret-key-123" not in str(redacted)
    assert "Bearer" not in str(redacted)
    # URL query 密钥被删除（redaction 的 scrub_url_query_secrets）。
    redacted_url = redacted.get("url")
    assert "api_key=" not in str(redacted_url)


def test_tool_request_summary_unknown_tool_is_empty() -> None:
    assert build_tool_request_summary("unknown_tool", {"a": 1, "b": 2}) == {}


# ---------------------------------------------------------------------------
# 17d：SEC/Serper 结果摘要只含结构化字段（accession/locator/标题/数量）
# ---------------------------------------------------------------------------


def test_tool_response_summary_sec_submissions_has_accession_locator() -> None:
    output = (
        '{"ok":true,"filings":['
        '{"form_type":"10-K","accession_number":"0000320193-25-000001",'
        '"filing_date":"2025-06-20","report_date":"2025-06-20"},'
        '{"form_type":"10-Q","accession_number":"0000320193-25-000002",'
        '"filing_date":"2025-01-30","report_date":"2025-01-30"}'
        "]}"
    )
    summary = build_tool_response_summary("sec_submissions", output)
    assert summary["ok"] is True
    assert summary["count"] == 2
    first = summary["filings"][0]
    assert first["accession"] == "0000320193-25-000001"
    assert "accn=0000320193-25-000001" in first["locator"]
    assert first["form"] == "10-K"


def test_tool_response_summary_web_search_has_title_url_count() -> None:
    output = (
        '{"ok":true,"count":1,"results":['
        '{"title":"Microsoft 10-K","url":"https://investor.microsoft.com/10k",'
        '"publisher":"Microsoft"}]}'
    )
    summary = build_tool_response_summary("web_search", output)
    assert summary["count"] == 1
    assert summary["results"][0]["title"] == "Microsoft 10-K"
    assert summary["results"][0]["url"] == "https://investor.microsoft.com/10k"


def test_tool_response_summary_failure_only_error_code() -> None:
    output = '{"ok":false,"error_code":"SEC_HTTP_429","message":"rate limited"}'
    summary = build_tool_response_summary("sec_company_facts", output)
    assert summary["ok"] is False
    assert summary["error_code"] == "SEC_HTTP_429"
    assert "SEC_HTTP_429" in summary["message"] or "rate limited" in summary["message"]


def test_tool_response_summary_invalid_json() -> None:
    summary = build_tool_response_summary("sec_submissions", "not-json")
    assert summary["ok"] is False
    assert summary["error_code"] == "INVALID_JSON"


# ---------------------------------------------------------------------------
# 17e：Prometheus /metrics 文本不含完整 payload 正文（labels 无 payload）
# ---------------------------------------------------------------------------


def test_metrics_endpoint_text_has_no_payload_content() -> None:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    text = generate_latest().decode("utf-8")
    # 所有指标只以 label 形式出现（tool/role/model/status 等），无 payload 正文。
    for sensitive in (
        "secret-content",
        "sk-secret-key-123",
        "Bearer",
        "Microsoft 10-K",
        "0000320193-25-000001",
    ):
        assert sensitive not in text
    assert CONTENT_TYPE_LATEST
