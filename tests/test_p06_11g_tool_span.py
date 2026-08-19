"""P06-11G: tool-level OTel span sanitization + error codes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from invest_research.infrastructure.performance import PerformanceRecorder
from invest_research.infrastructure.real_tools import _cached_execute
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache


def _success(namespace: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        kind="success",
        value=SimpleNamespace(model_dump=lambda mode="json": namespace),
    )


def test_tool_span_sanitized() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from invest_research.infrastructure.observability.tracing import setup_tracing

    exporter = InMemorySpanExporter()
    setup_tracing(service_name="test-p06-11g-span", exporter=exporter)

    budget = ToolBudget(caps={"web_search": 10})
    cache = ToolCallCache()
    recorder = PerformanceRecorder()

    def _run() -> Any:
        return _success({"snippet": "sk-super-secret-key", "url": "https://serper.dev/?q=sk-live"})

    _cached_execute(
        cache=cache,
        recorder=recorder,
        tool_name="web_search",
        params={"query": "MSFT SEC sk-super-secret-key", "as_of": "2025-12-31"},
        serialize_fn=lambda r: json.dumps(r.value.model_dump(mode="json"), default=str),
        execute_fn=_run,
        budget=budget,
    )
    spans = exporter.get_finished_spans()
    tool = [s for s in spans if s.name == "tool.web_search"]
    assert tool
    attrs = tool[0].attributes or {}
    assert attrs.get("tool.name") == "web_search"
    assert attrs.get("cache_hit") is False
    assert attrs.get("attempt_count") == 1
    assert attrs.get("budget_used") == 1
    assert attrs.get("budget_cap") == 10
    assert attrs.get("tool.status") == "success"
    assert "duration_ms" in attrs
    joined = json.dumps(dict(attrs), ensure_ascii=False)
    assert "sk-super-secret-key" not in joined
    assert "serper.dev" not in joined
    assert "query" not in attrs
    assert "snippet" not in attrs
    assert attrs.get("job_id") is None
    assert attrs.get("company") is None
    assert attrs.get("cik") is None


def test_budget_exhausted_span_error_code() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from invest_research.domain.errors import ErrorCode, is_retryable
    from invest_research.infrastructure.observability.tracing import setup_tracing

    exporter = InMemorySpanExporter()
    setup_tracing(service_name="test-p06-11g-budget", exporter=exporter)

    assert not is_retryable(ErrorCode.TOOL_BUDGET_EXHAUSTED)
    assert not is_retryable(ErrorCode.COMPANY_NOT_FOUND)
    assert not is_retryable(ErrorCode.SEC_PREFETCH_UNAVAILABLE)

    budget = ToolBudget(caps={"web_search": 1})
    budget.try_acquire("web_search")

    _cached_execute(
        cache=ToolCallCache(),
        recorder=PerformanceRecorder(),
        tool_name="web_search",
        params={"query": "q", "as_of": "2025-01-01"},
        serialize_fn=lambda r: "ok",
        execute_fn=lambda: (_ for _ in ()).throw(AssertionError("should not execute")),
        budget=budget,
    )
    spans = exporter.get_finished_spans()
    tool = [s for s in spans if s.name == "tool.web_search"]
    assert tool
    attrs = tool[-1].attributes or {}
    assert attrs.get("tool.status") == "failure"
    assert attrs.get("error_code") == ErrorCode.TOOL_BUDGET_EXHAUSTED.value
    assert attrs.get("retryable") is False
    assert attrs.get("budget_used") == 1
    assert attrs.get("budget_cap") == 1


def test_cache_hit_span_marks_cache_hit() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from invest_research.infrastructure.observability.tracing import setup_tracing

    exporter = InMemorySpanExporter()
    setup_tracing(service_name="test-p06-11g-cache", exporter=exporter)

    cache = ToolCallCache()
    key = cache.key("web_search", {"query": "q", "as_of": "2025-01-01"})
    cache.put(key, '{"ok": true, "count": 0, "results": []}')

    _cached_execute(
        cache=cache,
        recorder=PerformanceRecorder(),
        tool_name="web_search",
        params={"query": "q", "as_of": "2025-01-01"},
        serialize_fn=lambda r: "ok",
        execute_fn=lambda: (_ for _ in ()).throw(AssertionError("no exec")),
        budget=ToolBudget(caps={"web_search": 10}),
    )
    spans = exporter.get_finished_spans()
    hit = [s for s in spans if s.name == "tool.web_search"]
    assert hit
    assert (hit[-1].attributes or {}).get("cache_hit") is True
