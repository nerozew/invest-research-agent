"""P04-08：取消任务与安全点测试。

- pending 任务可取消（cancel_from_pending=True）；
- running 任务可取消（cancel_from_pending=False 但 cancel_from_running=True）；
- 终态任务不可取消（两个 writer 均 False）→ 幂等 no-op；
- 重复取消保持幂等（已 cancelled 会被 pending/running writer 拒绝 → no-op）；
- assert_cancellable 只接受 pending/running/cancelled，终态抛 InvalidStateTransitionError。
使用 fake writer（不依赖真实数据库/Celery），完全离线。
"""

from __future__ import annotations

import uuid

import pytest

from invest_research.application.cancellation import CancelResearchJobService
from invest_research.domain.status import JobStatus
from invest_research.domain.transitions import InvalidStateTransitionError


class FakeCancelWriter:
    """fake 取消端口：可配置 pending/running 是否可被取消。"""

    def __init__(self, *, pending_cancellable: bool, running_cancellable: bool) -> None:
        self._pending = pending_cancellable
        self._running = running_cancellable
        self.pending_calls: list[uuid.UUID] = []
        self.running_calls: list[uuid.UUID] = []

    def cancel_from_pending(self, job_id: uuid.UUID) -> bool:
        self.pending_calls.append(job_id)
        return self._pending

    def cancel_from_running(self, job_id: uuid.UUID) -> bool:
        self.running_calls.append(job_id)
        return self._running


def test_pending_job_can_be_cancelled() -> None:
    """pending -> cancelled：第一个安全点命中。"""
    job_id = uuid.uuid4()
    writer = FakeCancelWriter(pending_cancellable=True, running_cancellable=False)
    service = CancelResearchJobService(writer)

    did, already = service.cancel(job_id)

    assert did is True
    assert already is False
    assert writer.pending_calls == [job_id]
    assert writer.running_calls == []


def test_running_job_can_be_cancelled() -> None:
    """running -> cancelled：pending 失败后第二个安全点命中。"""
    job_id = uuid.uuid4()
    writer = FakeCancelWriter(pending_cancellable=False, running_cancellable=True)
    service = CancelResearchJobService(writer)

    did, already = service.cancel(job_id)

    assert did is True
    assert already is False
    assert writer.pending_calls == [job_id]
    assert writer.running_calls == [job_id]


def test_terminal_job_cannot_be_cancelled() -> None:
    """终态任务：两个 writer 都 False -> 幂等 no-op。"""
    job_id = uuid.uuid4()
    writer = FakeCancelWriter(pending_cancellable=False, running_cancellable=False)
    service = CancelResearchJobService(writer)

    did, already = service.cancel(job_id)

    assert did is False
    assert already is True


def test_repeat_cancel_is_idempotent() -> None:
    """重复取消：已 cancelled 的任务再次 cancel -> no-op（不改变状态不报错）。"""
    job_id = uuid.uuid4()
    writer = FakeCancelWriter(pending_cancellable=False, running_cancellable=False)
    service = CancelResearchJobService(writer)

    did1, already1 = service.cancel(job_id)
    did2, already2 = service.cancel(job_id)

    assert (did1, already1) == (False, True)
    assert (did2, already2) == (False, True)


@pytest.mark.parametrize(
    "status",
    [JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED],
)
def test_assert_cancellable_rejects_terminal(status: JobStatus) -> None:
    """终态不可取消：assert_cancellable 抛 InvalidStateTransitionError。"""
    with pytest.raises(InvalidStateTransitionError):
        CancelResearchJobService.assert_cancellable(status)


def test_assert_cancellable_accepts_pending_running_cancelled() -> None:
    """pending/running/cancelled 均可接受（不抛错）。"""
    for status in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLED):
        CancelResearchJobService.assert_cancellable(status)
