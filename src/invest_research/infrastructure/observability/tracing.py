"""OpenTelemetry trace（P05-07）。

提供：
- ``setup_tracing``：初始化 TracerProvider + 导出器（默认内存/控制台）；
- ``get_tracer``：按名称取 tracer，供 FastAPI/Worker/Flow/工具创建 span；
- ``trace_id_from_context``：读取当前 span context 的 trace_id，供结构化日志关联。
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

__all__ = ["setup_tracing", "get_tracer", "trace_id_from_context"]

DEFAULT_SERVICE_NAME = "invest-research"


def setup_tracing(
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    exporter: object | None = None,
) -> TracerProvider:
    """初始化全局 TracerProvider。

    - 默认用 ``SimpleSpanProcessor + ConsoleSpanExporter``（本地可观测）；
    - 生产可传入 OTLP exporter。
    """
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: service_name})
    )
    if exporter is None:
        processor = SimpleSpanProcessor(ConsoleSpanExporter())
    else:
        processor = SimpleSpanProcessor(exporter)  # type: ignore[arg-type]
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return provider


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def trace_id_from_context() -> str | None:
    """返回当前 span context 的 trace_id（十六进制），无则 None。"""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.trace_id == 0:
        return None
    return format(ctx.trace_id, "032x")
