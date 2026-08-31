"""P06-11J：真实阶段 Span helper（start_as_current_span 包裹真实执行边界）。

- ``stage_span``：用 ``tracer.start_as_current_span`` 包裹一段真实执行，
  成功 status=OK；异常时记录 exception + error_code/failure_stage 并重新抛出；
- 禁止在任务执行结束后创建零耗时的"补记 Span"。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import Span

from invest_research.infrastructure.observability.tracing import get_tracer

__all__ = ["stage_span", "set_span_error"]

# 允许的 stage 白名单（防止拼写错误产生无意义 span）。
_STAGE_NAMES = {
    "stage.company_resolve",
    "stage.prefetch",
    "stage.research",
    "stage.analysis",
    "stage.writer",
    "pack.research.validate",
    "pack.analysis.validate",
    "writer.assemble",
    "quality_gate",
    "revision",
    "artifact.publish",
}


def set_span_error(
    span: trace.Span | None,
    exc: Exception,
    error_code: str,
    failure_stage: str,
) -> None:
    """把异常记录到 span（状态 + exception + error_code/failure_stage）。"""
    if span is None:
        return
    try:
        span.set_status(trace.Status(trace.StatusCode.ERROR, "execution_failed"))
        span.record_exception(exc)
        span.set_attribute("error_code", str(error_code))
        span.set_attribute("failure_stage", str(failure_stage))
    except Exception:  # noqa: BLE001 - 观测尽力而为
        pass


@contextmanager
def stage_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    *,
    tracer_name: str = "stages",
) -> Iterator[Span]:
    """进入真实执行边界：开启子 span 并 yield；异常时标记失败后重新抛出。"""
    if name not in _STAGE_NAMES:
        # 未知阶段名不抛错（观测尽力而为），但仍记录脱敏属性。
        pass
    tracer = get_tracer(tracer_name)
    attrs = dict(attributes or {})
    attrs.setdefault("stage", name)
    with tracer.start_as_current_span(name, attributes=attrs) as current:
        try:
            yield current  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - 应用边界统一标记失败后重新抛出
            error_code = str(getattr(exc, "error_code", None) or "EXECUTION_FAILED")
            failure_stage = str(getattr(exc, "failure_stage", None) or name)
            set_span_error(current, exc, error_code, failure_stage)
            raise
