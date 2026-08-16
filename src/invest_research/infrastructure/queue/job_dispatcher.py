"""Celery 生产的任务投递实现（P04-10A API→Worker 链路）。

用 ``celery_app.send_task`` 把 job_id 投递到稳定任务名
``invest_research.process_research_job``；仅 JSON 序列化（Celery app 已禁 pickle）。

模块导入零 Redis 连接：只在 dispatch() 被调用时 send_task（连接 Redis）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from invest_research.infrastructure.queue.constants import TASK_PROCESS_JOB

__all__ = ["CeleryJobDispatcher"]


class _TaskSender(Protocol):
    """Minimal Celery producer surface used by this adapter."""

    def send_task(
        self,
        name: str,
        args: list[str],
        *,
        queue: str,
        headers: dict[str, str],
    ) -> Any: ...


class CeleryJobDispatcher:
    """把 job_id 投递到 Celery 队列的生产实现。"""

    def __init__(self, celery_app: _TaskSender) -> None:
        self._app = celery_app

    def dispatch(
        self,
        job_id: uuid.UUID,
        *,
        trace_context: Mapping[str, str] | None = None,
    ) -> None:
        """Dispatch a job and propagate persisted or current trace context."""
        from invest_research.infrastructure.observability.tracing import (
            current_trace_carrier,
        )

        headers = dict(trace_context) if trace_context else current_trace_carrier()
        self._app.send_task(
            TASK_PROCESS_JOB,
            args=[str(job_id)],
            queue="research-jobs",
            headers=headers,
        )
