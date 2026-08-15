"""OpenTelemetry trace（P05-07 基础 + P06-05 本地链路配置）。

提供：
- ``setup_tracing``：初始化 TracerProvider + 导出器：
  - 默认（无 OTLP endpoint）：``SimpleSpanProcessor + ConsoleSpanExporter``（本地可观测）；
  - 配置 ``endpoint``（如 http://localhost:4318，对应本地 collector）：OTLP HTTP
    导出 + ``BatchSpanProcessor``（按 ``batch_interval_ms`` 批量上报）；
- ``build_span_exporter``：按 endpoint 选择导出器（纯函数，便于测试）；
- ``get_tracer`` / ``span``：按名称取 tracer / 开启一个 span（contextmanager）；
- ``trace_id_from_context``：读取当前 span context 的 trace_id，供结构化日志关联。

调用方（composition root / worker 入口）负责在进程启动时调用 ``setup_tracing``；
未调用时 OTel 使用 no-op provider，所有 span 零开销跳过（不影响普通测试）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

__all__ = [
    "setup_tracing",
    "build_span_exporter",
    "get_tracer",
    "span",
    "trace_id_from_context",
]

DEFAULT_SERVICE_NAME = "invest-research"


def build_span_exporter(endpoint: str | None) -> SpanExporter:
    """按 endpoint 选择导出器：OTLP HTTP（配置了端点）或控制台（本地默认）。"""
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=endpoint)
    return ConsoleSpanExporter()


def setup_tracing(
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    endpoint: str | None = None,
    exporter: object | None = None,
    batch_interval_ms: int = 5000,
    processor: object | None = None,
) -> TracerProvider:
    """初始化全局 TracerProvider。

    - ``endpoint``：OTLP HTTP 端点；None 时用控制台导出（本地可观测）；
    - ``exporter``：自定义导出器（测试可传 InMemorySpanExporter）；
    - ``processor``：自定义 span processor（测试可传 SimpleSpanProcessor + 内存导出）；
    - ``batch_interval_ms``：OTLP 批量导出间隔（毫秒）。
    """
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    if processor is not None:
        provider.add_span_processor(processor)  # type: ignore[arg-type]
    elif exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))  # type: ignore[arg-type]
    elif endpoint:
        otlp_exporter = build_span_exporter(endpoint)
        provider.add_span_processor(
            BatchSpanProcessor(otlp_exporter, schedule_delay_millis=batch_interval_ms)
        )
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    # OTel SDK 默认只允许设置一次全局 provider（Once 守卫）；测试与进程内重配
    # 需要"最后一次调用生效"的语义，这里重置守卫后覆盖（仅单线程启动期使用）。
    from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE  # noqa: PLC2701

    _TRACER_PROVIDER_SET_ONCE._done = False
    trace.set_tracer_provider(provider)
    return provider


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def span(
    name: str,
    attributes: dict[str, Any] | None = None,
    *,
    tracer_name: str = DEFAULT_SERVICE_NAME,
) -> Iterator[Any]:
    """开启一个 span（contextmanager）：``with span("flow.run", {...}): ...``。

    - 未调用 ``setup_tracing`` 时 OTel 是 no-op provider，这里零开销返回；
    - ``attributes``：span 属性（只放低基数/非敏感字段，不放 job_id 之外的
      高基数 label 之外的任何密钥）。
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(name, attributes=attributes) as current:
        yield current


def trace_id_from_context() -> str | None:
    """返回当前 span context 的 trace_id（十六进制），无则 None。"""
    current_span = trace.get_current_span()
    ctx = current_span.get_span_context()
    if ctx.trace_id == 0:
        return None
    return format(ctx.trace_id, "032x")
