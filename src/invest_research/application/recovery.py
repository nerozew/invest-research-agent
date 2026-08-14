"""步骤 lease 与 stale recovery（P05-03）。

依据 `docs/04-WORKFLOW-RELIABILITY.md` §3 状态机：
> Worker 崩溃后超过 lease 的 ``running`` 步骤由恢复任务标记为 retryable，再次入队。

设计（at-least-once execution）：
- worker 开始处理某步骤时把步骤置为 ``running`` 并记录 lease 过期时间
  （``started_at + lease_seconds``）；
- 恢复任务调用 ``StaleRecoveryService.recover()``：找出所有
  ``running`` 且 lease 已过期的步骤，把它们条件更新为
  ``failed_retryable``（乐观锁：仅当仍是 running 才更新，防止与
  真实慢执行的 worker 竞争覆盖）；
- 恢复次数被记录，供 Promise/日志观测（P05-03A 做启动自动恢复）。

依赖边界：本层只允许导入标准库与 domain；存储能力经
``StepLeaseStore`` 端口注入（production 由 infrastructure 提供，
测试用内存 fake）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class StaleStepSnapshot:
    """一个 running 步骤的只读快照（供恢复扫描）。"""

    job_id: UUID
    step_name: str
    started_at: datetime


class StepLeaseStore(Protocol):
    """步骤 lease 存储端口（production 用 SQL，测试用内存）。"""

    def list_running_steps(self) -> list[StaleStepSnapshot]: ...

    def mark_failed_retryable(self, job_id: UUID, step_name: str) -> bool:
        """条件更新：running -> failed_retryable；仅当当前是 running 才成功。"""
        ...


@dataclass(frozen=True)
class RecoverySettings:
    """恢复策略参数。"""

    # 步骤 lease 时长：超过该时长仍 running 视为 stale（worker 崩溃）
    lease_seconds: float = 300.0


@dataclass(frozen=True)
class RecoveryResult:
    """一次恢复扫描的结果。"""

    recovered_count: int
    checked_count: int
    stale_steps: list[StaleStepSnapshot]
    recovered_step_names: list[str]


class StaleRecoveryService:
    """把超过 lease 的 running 步骤标记为可重试（不直接重跑）。"""

    def __init__(
        self,
        store: StepLeaseStore,
        settings: RecoverySettings = RecoverySettings(),
    ) -> None:
        self._store = store
        self._settings = settings

    def recover(self, now: datetime | None = None) -> RecoveryResult:
        """扫描并恢复 stale 步骤。

        - ``now`` 缺省用 UTC now；
        - 只把 (started_at + lease_seconds) < now 的 running 步骤标记为
          failed_retryable，返回实际恢复数；
        - 用条件更新（mark_failed_retryable），避免覆盖刚被 worker
          重新拉起的步骤（at-least-once 下的安全恢复）。
        """
        current = now if now is not None else datetime.now(timezone.utc)
        running = self._store.list_running_steps()

        stale: list[StaleStepSnapshot] = []
        for step in running:
            started = step.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            lease_expires = started + timedelta(seconds=self._settings.lease_seconds)
            if lease_expires < current:
                stale.append(step)

        recovered: list[str] = []
        for step in stale:
            if self._store.mark_failed_retryable(step.job_id, step.step_name):
                recovered.append(step.step_name)

        return RecoveryResult(
            recovered_count=len(recovered),
            checked_count=len(running),
            stale_steps=stale,
            recovered_step_names=recovered,
        )
