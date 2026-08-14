"""P05-03 步骤 lease 与 stale recovery 测试。

验证目标（对齐 docs/04-WORKFLOW-RELIABILITY.md §3）：
> Worker 崩溃后超过 lease 的 ``running`` 步骤由恢复任务标记为 retryable。

- 未超过 lease 的 running 步骤不被恢复；
- 超过 lease 的 running 步骤被恢复为 failed_retryable；
- 恢复顺序/计数正确；条件更新失败（被真实 worker 抢回）不算恢复；
- 混合场景：部分过期、部分不过期。
测试用内存 fake store + 固定 now，不依赖真实时间。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from invest_research.application.recovery import (
    RecoverySettings,
    StaleRecoveryService,
    StaleStepSnapshot,
)


class FakeStepLeaseStore:
    """内存版步骤 lease 存储：记录 running 步骤和可被抢占的标记。"""

    def __init__(self, steps: list[StaleStepSnapshot]) -> None:
        self._steps = steps
        self._removed: set[tuple[uuid.UUID, str]] = set()

    def list_running_steps(self) -> list[StaleStepSnapshot]:
        return [
            s
            for s in self._steps
            if (s.job_id, s.step_name) not in self._removed
        ]

    def mark_failed_retryable(self, job_id: uuid.UUID, step_name: str) -> bool:
        self._removed.add((job_id, step_name))
        return True


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _step(
    step_name: str,
    started_at: datetime,
    *,
    job_id: uuid.UUID | None = None,
) -> StaleStepSnapshot:
    return StaleStepSnapshot(
        job_id=job_id or uuid.uuid4(),
        step_name=step_name,
        started_at=started_at,
    )


def _recover(
    store: FakeStepLeaseStore,
    now: datetime | None = None,
    lease_seconds: float = 300.0,
) -> object:
    from invest_research.application.recovery import RecoveryResult  # noqa: F401

    service = StaleRecoveryService(
        store,
        RecoverySettings(lease_seconds=lease_seconds),
    )
    return service.recover(now=now)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_no_stale_steps_when_lease_not_expired() -> None:
    """未超过 lease 的 running 步骤不被恢复。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore(
        [_step("step02", now - timedelta(seconds=120))]
    )
    result = _recover(store, now)
    assert result.recovered_count == 0
    assert result.checked_count == 1
    assert result.recovered_step_names == []


def test_stale_step_is_recovered() -> None:
    """超过 lease（默认 300s）的 running 步骤被恢复。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore(
        [_step("step02", now - timedelta(seconds=301))]
    )
    result = _recover(store, now)
    assert result.recovered_count == 1
    assert result.recovered_step_names == ["step02"]


def test_custom_lease_seconds() -> None:
    """短 lease（如 10s）更快判定 stale。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore(
        [_step("step02", now - timedelta(seconds=11))]
    )
    result = _recover(store, now, lease_seconds=10.0)
    assert result.recovered_count == 1


def test_naive_started_at_is_treated_as_utc() -> None:
    """无时区信息的 started_at 按 UTC 处理（与 now 比较安全）。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    naive_started = datetime(2026, 1, 1, 11, 54)  # 无 tzinfo，视为 UTC = 6 分钟前
    store = FakeStepLeaseStore([_step("step02", naive_started)])
    result = _recover(store, now, lease_seconds=300.0)
    assert result.recovered_count == 1


def test_condition_update_failure_counts_as_not_recovered() -> None:
    """条件更新失败（被真实 worker 抢回）不计入恢复数。"""

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    class FailingStore(FakeStepLeaseStore):
        def __init__(self) -> None:
            super().__init__([_step("step02", now - timedelta(seconds=600))])

        def mark_failed_retryable(self, job_id: uuid.UUID, step_name: str) -> bool:
            return False  # worker 已把它拉回 running

    service = StaleRecoveryService(
        FailingStore(), RecoverySettings(lease_seconds=300.0)
    )
    result = service.recover(now=now)
    assert result.recovered_count == 0
    # stale_steps 仍被识别为 stale（只是标记失败）
    assert len(result.stale_steps) == 1


def test_mixed_stale_and_fresh() -> None:
    """混合场景：仅过期的被恢复。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore(
        [
            _step("step01", now - timedelta(seconds=301)),  # stale
            _step("step02", now - timedelta(seconds=120)),  # fresh
            _step("step03", now - timedelta(seconds=600)),  # stale
        ]
    )
    result = _recover(store, now)
    assert result.recovered_count == 2
    assert result.checked_count == 3
    assert sorted(result.recovered_step_names) == ["step01", "step03"]
