"""P06-11K：DiagnosticCaptureSink 端口与 Job-local BoundedDiagnosticBuffer。

约束（任务文档“二、应用层端口”）：
- 每个 Job 一个独立实例，禁止全局共享；finalized 后拒绝再添加事件；
- Ring Buffer：max_events 超限丢弃最旧事件；
- 单事件大小：max_event_bytes 超限截断 Payload，truncated=True；
- Bundle 总大小：max_bundle_bytes 超限拒绝新事件；
- finalize_success/finalize_failure 幂等。

安全：进入 Buffer 的 payload 必须是已脱敏数据。Buffer 做纵深防御——
若 payload 仍含非空禁止键（reasoning_content/CoT）则丢弃事件并计数。
K-1 只做内存缓冲与收口状态标记；持久化落盘在 K-2 接入。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

from invest_research.application.diagnostics.models import (
    DiagnosticCapturePolicy,
    DiagnosticDirection,
    DiagnosticEvent,
    DiagnosticEventType,
    ValidationErrorEntry,
    calculate_sha256,
    measure_bytes,
    now_utc,
)
from invest_research.application.diagnostics.redaction import FORBIDDEN_KEYS

# 归一化后的禁止键集合（去掉 _/-），与 redaction._is_forbidden_key 口径一致。
_FORBIDDEN_NORMALIZED: frozenset[str] = frozenset(
    key.replace("_", "").replace("-", "") for key in FORBIDDEN_KEYS
)
# 收口事件类型（finalize 事件不受 finalized 守卫拒绝）。
_FINALIZE_EVENT_TYPES = frozenset(
    {DiagnosticEventType.FINALIZE_SUCCESS, DiagnosticEventType.FINALIZE_FAILURE}
)

__all__ = [
    "DiagnosticCaptureSink",
    "BoundedDiagnosticBuffer",
    "BufferStats",
]


class DiagnosticCaptureSink(Protocol):
    """诊断捕获端口：调用方通过它记录诊断事件。

    所有方法返回 DiagnosticEvent | None（None=被策略/容量拒绝），绝不抛异常。
    """

    def record_metadata(
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
    ) -> DiagnosticEvent | None: ...

    def record_payload(
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
        redacted_fields: list[str] | None = None,
        dropped_fields: list[str] | None = None,
    ) -> DiagnosticEvent | None: ...

    def record_validation_error(
        self,
        *,
        stage: str,
        component: str,
        errors: list[ValidationErrorEntry],
        payload_kind: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None: ...

    def record_exception(
        self,
        *,
        stage: str,
        component: str,
        error_code: str,
        message: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None: ...

    def finalize_success(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None: ...

    def finalize_failure(
        self,
        *,
        error_code: str,
        failure_stage: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None: ...


@dataclass
class BufferStats:
    """Buffer 容量统计（供 manifest 汇总，K-2 使用）。"""

    event_count: int = 0
    original_bytes: int = 0
    stored_bytes: int = 0
    truncated_count: int = 0
    redaction_count: int = 0
    dropped_count: int = 0
    dropped_events: int = 0
    over_budget_events: int = 0
    policy_violations: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "event_count": self.event_count,
            "original_bytes": self.original_bytes,
            "stored_bytes": self.stored_bytes,
            "truncated_count": self.truncated_count,
            "redaction_count": self.redaction_count,
            "dropped_count": self.dropped_count,
            "dropped_events": self.dropped_events,
            "over_budget_events": self.over_budget_events,
            "policy_violations": self.policy_violations,
        }


class BoundedDiagnosticBuffer:
    """Job-local 有界诊断缓冲（Ring Buffer）。构造时绑定 job_id；finalized 后拒绝添加。"""

    def __init__(self, *, job_id: str, policy: DiagnosticCapturePolicy) -> None:
        self._job_id = job_id
        self._policy = policy
        # RLock：_append_finalize 在持有锁时调用 _append_locked（可重入）。
        self._lock = threading.RLock()
        self._events: list[DiagnosticEvent] = []
        self._sequence = 0
        self._finalized = False
        self._stats = BufferStats()
        self._enabled = policy.is_enabled

    @property
    def job_id(self) -> str:
        return self._job_id

    @property
    def policy(self) -> DiagnosticCapturePolicy:
        return self._policy

    @property
    def is_finalized(self) -> bool:
        with self._lock:
            return self._finalized

    @property
    def stats(self) -> BufferStats:
        with self._lock:
            return self._stats

    def events(self) -> list[DiagnosticEvent]:
        """事件列表副本（按 sequence 升序；K-2 持久化使用）。"""
        with self._lock:
            return list(self._events)

    # ------------------------------------------------------------------
    # DiagnosticCaptureSink 实现
    # ------------------------------------------------------------------

    def record_metadata(
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
    ) -> DiagnosticEvent | None:
        if not self._enabled:
            return None
        event = DiagnosticEvent.metadata_only(
            sequence=self._next_sequence(),
            job_id=self._job_id,
            data=data,
            original_size=measure_bytes(data),
            content_type=content_type,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            component=component,
            payload_kind=payload_kind,
            direction=direction,
        )
        return self._append(event)

    def record_payload(
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
        redacted_fields: list[str] | None = None,
        dropped_fields: list[str] | None = None,
    ) -> DiagnosticEvent | None:
        if not self._enabled:
            return None
        if not self._policy.keeps_payload:
            return self.record_metadata(
                stage=stage,
                component=component,
                payload_kind=payload_kind,
                data=data,
                direction=direction,
                content_type=content_type,
                trace_id=trace_id,
                span_id=span_id,
            )
        event = DiagnosticEvent(
            sequence=self._next_sequence(),
            timestamp=now_utc(),
            job_id=self._job_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            component=component,
            event_type=DiagnosticEventType.PAYLOAD,
            direction=direction,
            payload_kind=payload_kind,
            content_type=content_type,
            original_size=measure_bytes(data),
            stored_size=0,
            truncated=False,
            redacted_fields=list(redacted_fields or []),
            dropped_fields=list(dropped_fields or []),
            sha256=calculate_sha256(data),
            payload=data,
        )
        return self._append(event)

    def record_validation_error(
        self,
        *,
        stage: str,
        component: str,
        errors: list[ValidationErrorEntry],
        payload_kind: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None:
        if not self._enabled:
            return None
        event = DiagnosticEvent(
            sequence=self._next_sequence(),
            timestamp=now_utc(),
            job_id=self._job_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            component=component,
            event_type=DiagnosticEventType.VALIDATION_ERROR,
            payload_kind=payload_kind,
            validation_errors=list(errors),
        )
        return self._append(event)

    def record_exception(
        self,
        *,
        stage: str,
        component: str,
        error_code: str,
        message: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None:
        if not self._enabled:
            return None
        event = DiagnosticEvent(
            sequence=self._next_sequence(),
            timestamp=now_utc(),
            job_id=self._job_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            component=component,
            event_type=DiagnosticEventType.EXCEPTION,
            error_code=error_code,
            payload=message,
        )
        return self._append(event)

    def finalize_success(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None:
        if not self._enabled:
            return None
        event = DiagnosticEvent(
            sequence=self._next_sequence(),
            timestamp=now_utc(),
            job_id=self._job_id,
            trace_id=trace_id,
            span_id=span_id,
            event_type=DiagnosticEventType.FINALIZE_SUCCESS,
        )
        return self._append_finalize(event)

    def finalize_failure(
        self,
        *,
        error_code: str,
        failure_stage: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> DiagnosticEvent | None:
        if not self._enabled:
            return None
        event = DiagnosticEvent(
            sequence=self._next_sequence(),
            timestamp=now_utc(),
            job_id=self._job_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=failure_stage,
            event_type=DiagnosticEventType.FINALIZE_FAILURE,
            error_code=error_code,
        )
        return self._append_finalize(event)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def _append_finalize(self, event: DiagnosticEvent) -> DiagnosticEvent | None:
        """追加收口事件并置 finalized（幂等：重复收口返回 None）。"""
        with self._lock:
            if self._finalized:
                return None
            result = self._append_locked(event)
            if result is not None:
                self._finalized = True
            return result

    def _append(self, event: DiagnosticEvent) -> DiagnosticEvent | None:
        """容量判定后写入 Ring Buffer（finalize 事件不受 finalized 守卫拒绝）。"""
        with self._lock:
            if self._finalized and event.event_type not in _FINALIZE_EVENT_TYPES:
                return None
            return self._append_locked(event)

    def _append_locked(self, event: DiagnosticEvent) -> DiagnosticEvent | None:
        """锁内实现：policy 违规 → 单事件截断 → Bundle 预算 → Ring Buffer 淘汰。"""
        if _find_policy_violations(event, _FORBIDDEN_NORMALIZED):
            self._stats.policy_violations += 1
            return None

        size = measure_bytes(event.model_dump(mode="json"))
        if size > self._policy.max_event_bytes:
            event = self._truncate_payload(event)
            size = measure_bytes(event.model_dump(mode="json"))
            self._stats.truncated_count += 1
        # 回写实际存储字节（stored_size=事件完整序列化大小）
        event = event.model_copy(update={"stored_size": size})

        if self._stats.stored_bytes + size > self._policy.max_bundle_bytes:
            self._stats.over_budget_events += 1
            return None

        while len(self._events) >= self._policy.max_events:
            evicted = self._events.pop(0)
            self._stats.dropped_events += 1
            self._stats.original_bytes -= evicted.original_size
            self._stats.stored_bytes -= evicted.stored_size

        self._events.append(event)
        self._stats.event_count += 1
        self._stats.original_bytes += event.original_size
        self._stats.stored_bytes += event.stored_size
        self._stats.redaction_count += len(event.redacted_fields)
        self._stats.dropped_count += len(event.dropped_fields)
        return event

    def _truncate_payload(self, event: DiagnosticEvent) -> DiagnosticEvent:
        """循环截断事件 Payload，直到完整事件序列化 ≤ max_event_bytes。

        - 文本/JSON 先砍半，仍超限继续砍半；
        - 极端情况以缩小到 16 字符仍超限 → 置 payload=None（仅保留元数据）；
        - 返回截断后事件（truncated=True，stored_size=截断后完整序列化大小）。
        """
        if event.payload is None:
            return event.model_copy(update={"truncated": True})
        candidate = event
        budget = max(16, self._policy.max_event_bytes // 2)
        while True:
            payload = candidate.payload
            if isinstance(payload, str):
                new_payload: Any = payload[:budget]
            else:
                import json

                new_payload = json.dumps(payload, ensure_ascii=False, default=str)[:budget]
            candidate = event.model_copy(
                update={
                    "payload": new_payload,
                    "stored_size": 0,
                    "truncated": True,
                }
            )
            size = measure_bytes(candidate.model_dump(mode="json"))
            if size <= self._policy.max_event_bytes or budget <= 16:
                if size > self._policy.max_event_bytes:
                    # 极端：截到 16 字符仍超限 → 丢弃 Payload，仅保留元数据
                    candidate = event.model_copy(
                        update={"payload": None, "stored_size": 0, "truncated": True}
                    )
                candidate = candidate.model_copy(
                    update={"stored_size": measure_bytes(candidate.model_dump(mode="json"))}
                )
                return candidate
            budget = max(16, budget // 2)


def _find_policy_violations(event: DiagnosticEvent, forbidden: frozenset[str]) -> list[str]:
    """纵深防御：检查 payload 是否仍含非空禁止键（reasoning_content/CoT）。"""
    target = event.payload
    if not isinstance(target, dict):
        return []
    violations: list[str] = []
    for key, value in target.items():
        lowered = key.strip().lower().replace("_", "").replace("-", "")
        if lowered in forbidden and _has_content(value):
            violations.append(str(key))
    return violations


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
