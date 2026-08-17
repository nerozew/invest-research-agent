"""结构化日志辅助（P06-09C）。

提供：
- ``structured_extra``：把 Job 级低基数/非敏感字段收敛为 ``logging`` 的
  ``extra=`` dict；值为 None 的键被剔除，避免日志里出现 ``key=None`` 噪音；
- ``span_id_from_context``：读取当前 OTel span 的 span_id（十六进制，低位
  对齐），与 ``tracing.trace_id_from_context`` 对称，供日志与 Jaeger 链路关联。

用法（application 层通过函数内 lazy import 使用，保持依赖方向
``infrastructure -> application`` 不被破坏）：

    from invest_research.infrastructure.observability.logging import (
        span_id_from_context,
        structured_extra,
    )
    from invest_research.infrastructure.observability.tracing import trace_id_from_context

    logger.info(
        "job_flow_succeeded",
        extra=structured_extra(
            job_id=str(job_id),
            trace_id=trace_id_from_context(),
            span_id=span_id_from_context(),
        ),
    )

``extra`` 的字段都是低基数/非敏感；不得放入 job 内容、提示词、API key 或
任何个人/机密数据。
"""

from __future__ import annotations

__all__ = ["structured_extra", "span_id_from_context"]


def span_id_from_context() -> str | None:
    """返回当前 span context 的 span_id（十六进制），无则 None。"""
    from opentelemetry import trace

    current_span = trace.get_current_span()
    ctx = current_span.get_span_context()
    if ctx.span_id == 0:
        return None
    # span_id 是 64 位，按 16 位十六进制对齐（与 OTel 控制台导出格式一致）
    return format(ctx.span_id, "016x")


def structured_extra(
    *,
    job_id: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> dict[str, str]:
    """构建结构化日志 extra；值为 None 的键不进入返回 dict。

    - job_id / stage / error_code / trace_id / span_id 均为低基数/非敏感字段；
    - 只返回非 None 键，日志处理器不会看到 ``key=None`` 的占位噪音。
    """
    extra: dict[str, str] = {}
    if job_id is not None:
        extra["job_id"] = str(job_id)
    if stage is not None:
        extra["stage"] = stage
    if error_code is not None:
        extra["error_code"] = error_code
    if trace_id is not None:
        extra["trace_id"] = trace_id
    if span_id is not None:
        extra["span_id"] = span_id
    return extra
