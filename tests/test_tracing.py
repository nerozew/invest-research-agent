"""P05-07 OpenTelemetry trace 测试。"""

from __future__ import annotations

from invest_research.infrastructure.observability.tracing import (
    get_tracer,
    setup_tracing,
    trace_id_from_context,
)


def test_setup_tracing_and_create_span() -> None:
    provider = setup_tracing()
    assert provider is not None

    tracer = get_tracer("test.trace")
    with tracer.start_as_current_span("test_span") as span:
        span.set_attribute("job_id", "fake-uuid")
        trace_id = trace_id_from_context()
        assert trace_id is not None
        assert len(trace_id) == 32  # 16 字节十六进制

    # 离开 span 后无当前 context
    assert trace_id_from_context() is None


def test_trace_id_is_hex() -> None:
    setup_tracing()
    tracer = get_tracer("test.hex")
    with tracer.start_as_current_span("span_hex"):
        tid = trace_id_from_context()
        assert tid is not None
        int(tid, 16)  # 能按 16 进制解析
