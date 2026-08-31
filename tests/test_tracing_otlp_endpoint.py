"""P06-06C：OTLP HTTP trace endpoint 规范化测试。

验证目标：
- 基础地址 http://otel-collector:4318 → 追加 /v1/traces；
- 末尾斜杠处理；
- 已含 /v1/traces 不重复拼接；
- None/空字符串回退 None；
- build_span_exporter 使用规范化端点。
"""

from __future__ import annotations

import pytest

from invest_research.infrastructure.observability.tracing import (
    build_span_exporter,
    normalize_otlp_endpoint,
)


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://otel-collector:4318", "http://otel-collector:4318/v1/traces"),
        ("http://otel-collector:4318/", "http://otel-collector:4318/v1/traces"),
        ("http://localhost:4318", "http://localhost:4318/v1/traces"),
        (
            "http://otel-collector:4318/v1/traces",
            "http://otel-collector:4318/v1/traces",
        ),
        (
            "http://otel-collector:4318/v1/traces/",
            "http://otel-collector:4318/v1/traces/",
        ),
        ("  http://host:4318  ", "http://host:4318/v1/traces"),
    ],
)
def test_normalize_otlp_endpoint_appends_or_preserves_path(
    endpoint: str, expected: str
) -> None:
    assert normalize_otlp_endpoint(endpoint) == expected


@pytest.mark.parametrize("endpoint", [None, "", "   "])
def test_normalize_otlp_endpoint_none_for_missing(endpoint: str | None) -> None:
    assert normalize_otlp_endpoint(endpoint) is None


def test_normalize_otlp_endpoint_rejects_trailing_path_without_v1_traces() -> None:
    # 其它路径不猜测：统一追加 /v1/traces（如用户配了基础地址带其它路径则结果可读）。
    result = normalize_otlp_endpoint("http://host:4318/foo")
    assert result == "http://host:4318/foo/v1/traces"


def test_build_span_exporter_uses_normalized_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class _FakeExporter:
        def __init__(self, *, endpoint: str) -> None:
            captured["endpoint"] = endpoint

    # build_span_exporter 内部延迟导入 OTLPSpanExporter；patch 模块属性路径。
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        _FakeExporter,
    )
    build_span_exporter("http://otel-collector:4318")
    assert captured["endpoint"] == "http://otel-collector:4318/v1/traces"
