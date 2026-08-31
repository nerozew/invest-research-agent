"""OpenTelemetry trace（P05-07 基础 + P06-05 本地链路配置 + P06-06C 端点修复）。

提供：
- ``setup_tracing``：初始化 TracerProvider + 导出器：
  - 默认（无 OTLP endpoint）：``SimpleSpanProcessor + ConsoleSpanExporter``（本地可观测）；
  - 配置 ``endpoint``（如 http://localhost:4318，对应本地 collector）：OTLP HTTP
    导出 + ``BatchSpanProcessor``（按 ``batch_interval_ms`` 批量上报）；
- ``normalize_otlp_endpoint``：把 OTLP HTTP 基础地址规范化为含 ``/v1/traces`` 的
  完整导出端点（P06-06C：OTLPSpanExporter 要求完整路径，否则 404）；
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
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
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
    "normalize_otlp_endpoint",
    "get_tracer",
    "span",
    "trace_id_from_context",
    "current_trace_ids",
    "current_trace_carrier",
    "extract_trace_context",
]

DEFAULT_SERVICE_NAME = "invest-research"

# OTLP HTTP 协议要求导出 POST 到完整路径 http://host:port/v1/traces。
# collector 环境变量 OTEL_EXPORTER_OTLP_ENDPOINT 约定只配置基础地址（不含路径），
# SDK 客户端要求显式传给 OTLPSpanExporter(endpoint=...) 的必须是含 /v1/traces 的完整 URL。
_OTLP_TRACES_PATH = "/v1/traces"


def normalize_otlp_endpoint(endpoint: str | None) -> str | None:
    """把 OTLP HTTP 基础地址规范化为含 ``/v1/traces`` 的完整导出端点。

    - ``None`` / 空字符串 → None（调用方回退控制台导出）；
    - 已含 ``/v1/traces``（任意结尾）→ 原样返回；
    - 基础地址 → 追加 ``/v1/traces``；
    - 正确处理末尾斜杠：``http://host:4318/`` → ``http://host:4318/v1/traces``。
    """
    if endpoint is None:
        return None
    stripped = endpoint.strip()
    if not stripped:
        return None
    if stripped.rstrip("/").endswith(_OTLP_TRACES_PATH):
        return stripped
    return f"{stripped.rstrip('/')}{_OTLP_TRACES_PATH}"


def build_span_exporter(endpoint: str | None) -> SpanExporter:
    """按 endpoint 选择导出器：OTLP HTTP（配置了端点）或控制台（本地默认）。

    P06-06C：传给 OTLPSpanExporter 前先经 ``normalize_otlp_endpoint`` 规范化，
    修正 http://otel-collector:4318 导致的 `/v1/traces` 缺失（404 Not Found）。
    """
    normalized = normalize_otlp_endpoint(endpoint)
    if normalized:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=normalized)
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
    context: Context | None = None,
) -> Iterator[Any]:
    """开启一个 span（contextmanager）：``with span("flow.run", {...}): ...``。

    - 未调用 ``setup_tracing`` 时 OTel 是 no-op provider，这里零开销返回；
    - ``attributes``：span 属性（只放低基数/非敏感字段，不放 job_id 之外的
      高基数 label 之外的任何密钥）。
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(
        name,
        context=context,
        attributes=attributes,
    ) as current:
        yield current


def current_trace_carrier() -> dict[str, str]:
    """Serialize the active OTel context for an outbox or Celery message."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: object | None) -> Context | None:
    """Extract a parent context from untrusted queue headers."""
    if not isinstance(carrier, dict):
        return None
    safe = {
        str(key): value.decode() if isinstance(value, bytes) else str(value)
        for key, value in carrier.items()
        if isinstance(key, str) and isinstance(value, (str, bytes))
    }
    if not safe:
        return None
    return extract(safe)


def trace_id_from_context() -> str | None:
    """返回当前 span context 的 trace_id（十六进制），无则 None。"""
    current_span = trace.get_current_span()
    ctx = current_span.get_span_context()
    if ctx.trace_id == 0:
        return None
    return format(ctx.trace_id, "032x")


def current_trace_ids() -> tuple[str | None, str | None]:
    """返回当前 span context 的 (trace_id, span_id)，均为十六进制；无则 (None, None)。

    P06-11K-5：诊断事件 / 结构化日志 / 时间线与 Jaeger 使用同一 trace_id，
    保证「同一任务所有诊断事件 trace_id 一致，可交叉定位」。
    """
    current_span = trace.get_current_span()
    ctx = current_span.get_span_context()
    if ctx.trace_id == 0:
        return None, None
    trace_id = format(ctx.trace_id, "032x")
    span_id = format(ctx.span_id, "016x") if ctx.span_id and ctx.span_id != 0 else None
    return trace_id, span_id
