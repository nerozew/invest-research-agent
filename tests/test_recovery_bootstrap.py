"""P05-03A Worker 启动自动 stale recovery 与恢复计数测试。

验证目标：
- worker 启动调用 `run_startup_recovery` 执行一次性恢复扫描；
- 恢复计数（RecoveryCounter）累计每次启动恢复的步骤数，且保存最近一次扫描；
- 无 stale 步骤时恢复数为 0，计数器不增长。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from invest_research.application.recovery import (
    RecoveryCounter,
    RecoverySettings,
    StaleRecoveryService,
    StaleStepSnapshot,
)
from invest_research.infrastructure.queue.recovery_bootstrap import run_startup_recovery


class FakeStepLeaseStore:
    """内存版步骤 lease 存储（与 P05-03 fake 一致）。"""

    def __init__(self, steps: list[StaleStepSnapshot]) -> None:
        self._steps = steps
        self._removed: set[tuple[uuid.UUID, str]] = set()

    def list_running_steps(self) -> list[StaleStepSnapshot]:
        return [s for s in self._steps if (s.job_id, s.step_name) not in self._removed]

    def mark_failed_retryable(self, job_id: uuid.UUID, step_name: str) -> bool:
        self._removed.add((job_id, step_name))
        return True


def _step(step_name: str, started_at: datetime) -> StaleStepSnapshot:
    return StaleStepSnapshot(job_id=uuid.uuid4(), step_name=step_name, started_at=started_at)


def test_startup_recovery_recovers_stale_and_updates_counter() -> None:
    """启动恢复恢复 stale 步骤，并把计数写入 counter。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore(
        [
            _step("step02", now - timedelta(seconds=301)),  # stale
            _step("step03", now - timedelta(seconds=120)),  # fresh
        ]
    )
    service = StaleRecoveryService(store, RecoverySettings(lease_seconds=300.0))
    counter = RecoveryCounter()

    count = run_startup_recovery(service, counter, now=lambda: now)

    assert count == 1
    assert counter.total_recovered == 1
    assert counter.last_scan is not None
    assert counter.last_scan.recovered_count == 1
    assert counter.last_scan.checked_count == 2


def test_startup_recovery_with_no_stale_returns_zero() -> None:
    """没有 stale 步骤时恢复数为 0，计数器不增长。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore([_step("step02", now - timedelta(seconds=120))])
    service = StaleRecoveryService(store, RecoverySettings(lease_seconds=300.0))
    counter = RecoveryCounter()

    count = run_startup_recovery(service, counter, now=lambda: now)

    assert count == 0
    assert counter.total_recovered == 0


def test_counter_accumulates_across_multiple_startups() -> None:
    """多次启动恢复时计数器累计。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store = FakeStepLeaseStore([_step("step02", now - timedelta(seconds=301))])
    service = StaleRecoveryService(store, RecoverySettings(lease_seconds=300.0))
    counter = RecoveryCounter()

    # 第一次启动：恢复 1 个
    assert run_startup_recovery(service, counter, now=lambda: now) == 1
    # 第二次启动：相同 store，步骤已被移除 → 0
    assert run_startup_recovery(service, counter, now=lambda: now) == 0

    # 计数器累计只保留第一次的 1
    assert counter.total_recovered == 1
