"""P06-11J：Job-local 执行时间线（脱敏工件 10_execution_timeline.jsonl）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from invest_research.infrastructure.observability.tracing import get_tracer

__all__ = ["ExecutionTimelineSink", "TimelineEvent"]

_LOGGER = logging.getLogger(__name__)

_TIMELINE_FILENAME = "10_execution_timeline.jsonl"


@dataclass(frozen=True)
class TimelineEvent:
    sequence: int
    timestamp: str
    stage: str
    event_type: str
    role: str | None = None
    tool_name: str | None = None
    call_index: int | None = None
    status: str | None = None
    duration_ms: float | None = None
    input_chars: int | None = None
    output_chars: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        """序列化为 JSON dict（None 字段省略）。"""
        payload: dict[str, Any] = {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "stage": self.stage,
            "event_type": self.event_type,
        }
        if self.role is not None:
            payload["role"] = self.role
        if self.tool_name is not None:
            payload["tool_name"] = self.tool_name
        if self.call_index is not None:
            payload["call_index"] = self.call_index
        if self.status is not None:
            payload["status"] = self.status
        if self.duration_ms is not None:
            payload["duration_ms"] = round(self.duration_ms, 3)
        if self.input_chars is not None:
            payload["input_chars"] = self.input_chars
        if self.output_chars is not None:
            payload["output_chars"] = self.output_chars
        if self.input_tokens is not None:
            payload["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            payload["output_tokens"] = self.output_tokens
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.trace_id is not None:
            payload["trace_id"] = self.trace_id
        if self.span_id is not None:
            payload["span_id"] = self.span_id
        return payload


def _current_trace_ids() -> tuple[str | None, str | None]:
    """读取当前 OTel 上下文的 trace_id / span_id（16 进制，无则 None）。"""
    try:
        current = get_tracer("timeline").start_span("timeline_probe")
        ctx = current.get_span_context()
        current.end()
        if ctx.trace_id == 0:
            return None, None
        trace_id = format(ctx.trace_id, "032x")
        span_id = format(ctx.span_id, "016x") if ctx.span_id else None
        return trace_id, span_id
    except Exception:  # noqa: BLE001 - 观测尽力而为
        return None, None


class ExecutionTimelineSink:
    """Job-local 执行时间线写入器（append-only）。"""

    def __init__(
        self,
        *,
        path: Path | str | None = None,
        artifact_root: Path | str | None = None,
        job_id: str | None = None,
    ) -> None:
        self._path: Path | None = None
        if path is not None:
            self._path = Path(path)
        elif artifact_root is not None and job_id is not None:
            self._path = Path(artifact_root) / job_id / _TIMELINE_FILENAME
        self._handle: Any = None
        self._sequence = 0
        self._closed = False

    def record(
        self,
        *,
        stage: str,
        event_type: str,
        role: str | None = None,
        tool_name: str | None = None,
        call_index: int | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        input_chars: int | None = None,
        output_chars: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_code: str | None = None,
    ) -> None:
        """追加一条脱敏事件。任何失败都不影响主业务（脱敏日志）。"""
        if self._closed or self._path is None:
            return
        try:
            import datetime

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            trace_id, span_id = _current_trace_ids()
            self._sequence += 1
            event = TimelineEvent(
                sequence=self._sequence,
                timestamp=now,
                stage=stage,
                event_type=event_type,
                role=role,
                tool_name=tool_name,
                call_index=call_index,
                status=status,
                duration_ms=duration_ms,
                input_chars=input_chars,
                output_chars=output_chars,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_code=error_code,
                trace_id=trace_id,
                span_id=span_id,
            )
            self._ensure_handle()
            if self._handle is None:
                return
            self._handle.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
            self._handle.flush()
        except Exception:  # noqa: BLE001 - Timeline 写入失败不得影响主业务
            _LOGGER.warning("execution_timeline_record_failed stage=%s", stage)

    def close(self) -> None:
        """关闭文件句柄（幂等；失败不影响主业务）。"""
        if self._closed:
            return
        self._closed = True
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("execution_timeline_close_failed")
            finally:
                self._handle = None

    def _ensure_handle(self) -> None:
        """惰性打开文件（创建父目录；失败仅告警）。"""
        if self._handle is not None or self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8")
        except Exception:  # noqa: BLE001
            _LOGGER.warning("execution_timeline_open_failed path=%s", self._path)
            self._handle = None
