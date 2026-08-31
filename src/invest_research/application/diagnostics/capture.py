"""P06-11K-3：DiagnosticCapture —— 业务边界的"先脱敏再入缓冲"封装。

设计：
- 包装 ``BoundedDiagnosticBuffer`` + ``RecursiveRedactor`` + ``RedactionContext``；
- ``capture(...)``：任意业务数据 → 递归脱敏 → ``record_payload``（或 metadata 模式）；
- ``record_validation_error(...)`` / ``record_exception(...)``：诊断事件；
- ``finalize_success`` / ``finalize_failure``：收口（幂等，最终由调用方落盘）；
- capture 为 None 时（DIAGNOSTIC_CAPTURE_MODE=off）所有方法 no-op，绝不抛异常。

安全：capture 内部先调用 ``redact_payload`` 再入 Buffer，保证进入 Buffer 的
一定是脱敏数据；reasoning_content/CoT 由 redaction 丢弃 + Buffer 纵深防御。
"""

from __future__ import annotations

from typing import Any

from invest_research.application.diagnostics.models import (
    DiagnosticDirection,
    ValidationErrorEntry,
)
from invest_research.application.diagnostics.redaction import (
    RedactionContext,
    RedactionResult,
    redact_payload,
)
from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer

__all__ = [
    "DiagnosticCapture",
    "CapturedPayload",
]


class CapturedPayload:
    """脱敏后 payload 与脱敏统计（供调用方可选校验/展示）。"""

    __slots__ = ("data", "result")

    def __init__(self, data: Any, result: RedactionResult) -> None:
        self.data = data
        self.result = result


class DiagnosticCapture:
    """Job-local 诊断捕获器：先脱敏再入 Buffer（off 时全部 no-op）。"""

    def __init__(
        self,
        *,
        buffer: BoundedDiagnosticBuffer | None,
        context: RedactionContext | None = None,
    ) -> None:
        self._buffer = buffer
        self._context = context or RedactionContext()

    @property
    def buffer(self) -> BoundedDiagnosticBuffer | None:
        return self._buffer

    @property
    def is_enabled(self) -> bool:
        return self._buffer is not None and self._buffer.policy.is_enabled

    def capture(
        self,
        *,
        stage: str,
        component: str,
        payload_kind: str,
        data: Any,
        direction: DiagnosticDirection | None = None,
        content_type: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> CapturedPayload | None:
        """业务数据 → 脱敏 → 入 Buffer。返回脱敏结果；off/容量拒绝返回 None。"""
        if self._buffer is None:
            return None
        redacted = redact_payload(data, context=self._context)
        self._buffer.record_payload(
            stage=stage,
            component=component,
            payload_kind=payload_kind,
            data=redacted.data,
            direction=direction,
            content_type=content_type,
            trace_id=trace_id,
            span_id=span_id,
            redacted_fields=redacted.redacted_fields,
            dropped_fields=redacted.dropped_fields,
        )
        return CapturedPayload(data=redacted.data, result=redacted)

    def record_validation_error(
        self,
        *,
        stage: str,
        component: str,
        errors: list[ValidationErrorEntry],
        payload_kind: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        if self._buffer is None:
            return
        self._buffer.record_validation_error(
            stage=stage,
            component=component,
            errors=errors,
            payload_kind=payload_kind,
            trace_id=trace_id,
            span_id=span_id,
        )

    def record_exception(
        self,
        *,
        stage: str,
        component: str,
        error_code: str,
        message: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        if self._buffer is None:
            return
        self._buffer.record_exception(
            stage=stage,
            component=component,
            error_code=error_code,
            message=message,
            trace_id=trace_id,
            span_id=span_id,
        )

    def finalize_success(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        if self._buffer is None:
            return
        self._buffer.finalize_success(trace_id=trace_id, span_id=span_id)

    def finalize_failure(
        self,
        *,
        error_code: str,
        failure_stage: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        if self._buffer is None:
            return
        self._buffer.finalize_failure(
            error_code=error_code,
            failure_stage=failure_stage,
            trace_id=trace_id,
            span_id=span_id,
        )
