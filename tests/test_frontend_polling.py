"""P04-UI-03：前端轮询服务测试。

使用 fake fetch（返回注入的 JobSnapshot 序列）+ fake sleep（不真实等待），
验证：pending→running→终态自动停止、次数耗尽超时、404/503 转 PollingError。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from invest_research.domain.status import JobStatus
from invest_research.frontend.errors import ApiNotFoundError
from invest_research.frontend.models import JobSnapshot
from invest_research.frontend.polling import PollingError, poll_until_terminal


def _snapshot(status: JobStatus, job_id: str) -> JobSnapshot:
    return JobSnapshot(
        job_id=uuid.UUID(job_id),
        status=status,
        current_step=None,
        error_code=None,
        error_message=None,
        started_at=datetime(2026, 8, 13, 9, 0, 0),
        completed_at=datetime(2026, 8, 13, 9, 2, 0),
        duration_seconds=120.0,
        steps=(),
    )


def _noop_sleep(_: float) -> None:
    """fake sleep：不真实等待。"""


def test_polls_until_terminal_and_stops() -> None:
    """pending → running → succeeded：到达终态自动停止，不继续轮询。"""
    job_id = "12345678-1234-1234-1234-123456789abc"
    calls: list[str] = []

    states = [
        _snapshot(JobStatus.PENDING, job_id),
        _snapshot(JobStatus.RUNNING, job_id),
        _snapshot(JobStatus.SUCCEEDED, job_id),
    ]

    def fetch() -> JobSnapshot:
        calls.append("fetch")
        return states[min(len(calls) - 1, len(states) - 1)]

    snapshot, timed_out = poll_until_terminal(
        fetch, max_polls=10, interval_seconds=0.1, sleep=_noop_sleep
    )

    assert timed_out is False
    assert snapshot.status == JobStatus.SUCCEEDED
    assert len(calls) == 3


def test_failed_is_terminal_and_stops() -> None:
    """failed 也是终态：停止轮询。"""
    job_id = "22345678-1234-1234-1234-123456789abc"

    def fetch() -> JobSnapshot:
        return _snapshot(JobStatus.FAILED, job_id)

    snapshot, timed_out = poll_until_terminal(
        fetch, max_polls=5, interval_seconds=0.1, sleep=_noop_sleep
    )

    assert timed_out is False
    assert snapshot.status == JobStatus.FAILED


def test_polls_exhaustion_returns_timed_out() -> None:
    """一直 pending：轮询次数耗尽 → timed_out=True。"""
    job_id = "32345678-1234-1234-1234-123456789abc"
    calls: list[str] = []

    def fetch() -> JobSnapshot:
        calls.append("fetch")
        return _snapshot(JobStatus.PENDING, job_id)

    snapshot, timed_out = poll_until_terminal(
        fetch, max_polls=3, interval_seconds=0.1, sleep=_noop_sleep
    )

    assert timed_out is True
    assert snapshot.status == JobStatus.PENDING
    # 3 次遍历 + 最后 1 次收尾查询 = 4 次 fetch（3 次判断 + 1 次最终）
    assert len(calls) == 4


def test_404_raises_polling_error() -> None:
    """任务不存在（404）→ PollingError，UI 提示"任务不存在"。"""

    def fetch() -> JobSnapshot:
        raise ApiNotFoundError(404, "任务不存在")

    with pytest.raises(PollingError, match="任务不存在"):
        poll_until_terminal(fetch, max_polls=2, interval_seconds=0.1, sleep=_noop_sleep)
