"""Celery producer contract tests without a broker or worker runtime."""

from __future__ import annotations

import uuid
from typing import Any

from invest_research.infrastructure.observability.tracing import setup_tracing, span
from invest_research.infrastructure.queue.constants import TASK_PROCESS_JOB
from invest_research.infrastructure.queue.job_dispatcher import CeleryJobDispatcher


class _FakeCelery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], str, dict[str, str]]] = []

    def send_task(
        self,
        name: str,
        args: list[str],
        *,
        queue: str,
        headers: dict[str, str],
    ) -> Any:
        self.calls.append((name, args, queue, headers))
        return object()


def test_celery_dispatcher_serializes_job_and_trace_headers() -> None:
    setup_tracing()
    app = _FakeCelery()
    job_id = uuid.uuid4()

    with span("api.request"):
        CeleryJobDispatcher(app).dispatch(job_id)

    assert len(app.calls) == 1
    name, args, queue, headers = app.calls[0]
    assert name == TASK_PROCESS_JOB
    assert args == [str(job_id)]
    assert queue == "research-jobs"
    assert headers["traceparent"].startswith("00-")


def test_celery_dispatcher_prefers_persisted_trace_context() -> None:
    app = _FakeCelery()
    job_id = uuid.uuid4()
    persisted = {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}

    CeleryJobDispatcher(app).dispatch(job_id, trace_context=persisted)

    assert app.calls[0][3] == persisted
