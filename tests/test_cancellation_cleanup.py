"""P06-06B 收口：取消语义测试。

验证：
- CancelResearchJobService 取消成功后自动收口步骤（注入 CancelStepCleanup 端口）；
- 未注入 cleanup 时取消仍成功（向后兼容）；
- 已取消/终态任务重复取消不触发收口（幂等）。
"""

from __future__ import annotations

import uuid

from invest_research.application.cancellation import CancelResearchJobService


class _FakeCleanup:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    def cleanup_steps(self, job_id: uuid.UUID) -> None:
        self.calls.append(job_id)


class _FakeWriter:
    def __init__(self, *, pending_cancellable: bool, running_cancellable: bool) -> None:
        self._pending = pending_cancellable
        self._running = running_cancellable

    def cancel_from_pending(self, job_id: uuid.UUID) -> bool:
        return self._pending

    def cancel_from_running(self, job_id: uuid.UUID) -> bool:
        return self._running


def test_cancel_pending_triggers_cleanup() -> None:
    """pending 取消成功后调用 cleanup（收口步骤）。"""
    job_id = uuid.uuid4()
    cleanup = _FakeCleanup()
    service = CancelResearchJobService(
        writer=_FakeWriter(pending_cancellable=True, running_cancellable=False),
        cleanup=cleanup,
    )

    did_cancel, already = service.cancel(job_id)

    assert did_cancel is True
    assert already is False
    assert cleanup.calls == [job_id]


def test_cancel_running_triggers_cleanup() -> None:
    """running 取消成功后调用 cleanup（收口步骤）。"""
    job_id = uuid.uuid4()
    cleanup = _FakeCleanup()
    service = CancelResearchJobService(
        writer=_FakeWriter(pending_cancellable=False, running_cancellable=True),
        cleanup=cleanup,
    )

    did_cancel, already = service.cancel(job_id)

    assert did_cancel is True
    assert cleanup.calls == [job_id]


def test_cancel_terminal_does_not_trigger_cleanup() -> None:
    """终态/已取消任务：不调用 cleanup（幂等无变化）。"""
    job_id = uuid.uuid4()
    cleanup = _FakeCleanup()
    service = CancelResearchJobService(
        writer=_FakeWriter(pending_cancellable=False, running_cancellable=False),
        cleanup=cleanup,
    )

    did_cancel, already = service.cancel(job_id)

    assert did_cancel is False
    assert already is True
    assert cleanup.calls == []


def test_cancel_without_cleanup_still_succeeds() -> None:
    """未注入 cleanup（向后兼容）：取消仍成功。"""
    job_id = uuid.uuid4()
    service = CancelResearchJobService(
        writer=_FakeWriter(pending_cancellable=False, running_cancellable=True),
    )

    did_cancel, already = service.cancel(job_id)

    assert did_cancel is True
    assert already is False


def test_cleanup_failure_does_not_change_cancel_result() -> None:
    """cleanup 抛异常：取消结果不受影响（收口尽力而为）。"""

    class _BoomCleanup:
        def cleanup_steps(self, job_id: uuid.UUID) -> None:
            raise RuntimeError("cleanup boom")

    job_id = uuid.uuid4()
    service = CancelResearchJobService(
        writer=_FakeWriter(pending_cancellable=True, running_cancellable=False),
        cleanup=_BoomCleanup(),
    )

    did_cancel, already = service.cancel(job_id)

    assert did_cancel is True
    assert already is False
