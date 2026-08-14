"""Transactional Outbox：Job 创建事件可靠投递（P05-03B）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from invest_research.application.job_dispatcher import JobDispatcher

__all__ = [
    "EVENT_TYPE_JOB_CREATED",
    "OutboxEventSnapshot",
    "OutboxStore",
    "OutboxSettings",
    "OutboxRelayResult",
    "OutboxRelayCounter",
    "OutboxRelayService",
]

EVENT_TYPE_JOB_CREATED = "job_created"


@dataclass(frozen=True)
class OutboxEventSnapshot:
    """一条待投递 outbox 事件的只读快照。"""

    event_id: uuid.UUID
    job_id: uuid.UUID
    event_type: str
    attempts: int


class OutboxStore(Protocol):
    """Outbox 事件存储端口（production 用 SQL，测试用内存）。"""

    def find_undelivered(self, *, limit: int) -> list[OutboxEventSnapshot]: ...

    def find_by_job(
        self, job_id: uuid.UUID, event_type: str
    ) -> OutboxEventSnapshot | None: ...

    def mark_claimed(self, event_id: uuid.UUID) -> bool:
        """条件更新 pending → claimed；仅当仍是 pending 才成功（防并发重复）。"""
        ...

    def mark_sent(self, event_id: uuid.UUID) -> None: ...

    def requeue(self, event_id: uuid.UUID) -> None: ...

    def mark_failed(self, event_id: uuid.UUID) -> None: ...


@dataclass(frozen=True)
class OutboxSettings:
    """Outbox 重试策略。"""

    max_attempts: int = 5
    batch_size: int = 50


@dataclass(frozen=True)
class OutboxRelayResult:
    """一次投递/relay 的结果。"""

    sent_count: int
    checked_count: int
    failed_count: int


class OutboxRelayCounter:
    """Outbox 投递计数（供观测：Prometheus counter / 日志聚合）。"""

    def __init__(self) -> None:
        self._total_sent: int = 0
        self._last_relay: OutboxRelayResult | None = None

    @property
    def total_sent(self) -> int:
        return self._total_sent

    @property
    def last_relay(self) -> OutboxRelayResult | None:
        return self._last_relay

    def record(self, result: OutboxRelayResult) -> None:
        self._total_sent += result.sent_count
        self._last_relay = result


class OutboxRelayService:
    """把未投递 outbox 事件可靠投递给 JobDispatcher。"""

    def __init__(
        self,
        store: OutboxStore,
        dispatcher: JobDispatcher,
        settings: OutboxSettings = OutboxSettings(),
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._settings = settings

    def _deliver(self, event: OutboxEventSnapshot) -> bool:
        """投递单个事件；返回是否成功投递。"""
        if not self._store.mark_claimed(event.event_id):
            return False
        try:
            self._dispatcher.dispatch(event.job_id)
        except Exception:  # noqa: BLE001 - outbox 持久化失败状态
            if event.attempts + 1 >= self._settings.max_attempts:
                self._store.mark_failed(event.event_id)
            else:
                self._store.requeue(event.event_id)
            return False
        self._store.mark_sent(event.event_id)
        return True

    def publish(
        self,
        job_id: uuid.UUID,
        event_type: str = EVENT_TYPE_JOB_CREATED,
    ) -> OutboxRelayResult:
        """API 创建后即时投递；事件不存在时返回全 0。"""
        event = self._store.find_by_job(job_id, event_type)
        if event is None:
            return OutboxRelayResult(sent_count=0, checked_count=0, failed_count=0)
        sent = self._deliver(event)
        return OutboxRelayResult(
            sent_count=1 if sent else 0,
            checked_count=1,
            failed_count=0 if sent else 1,
        )

    def relay_once(self, *, limit: int | None = None) -> OutboxRelayResult:
        """批量恢复所有未投递事件（Worker 启动/定时任务调用）。"""
        pending = self._store.find_undelivered(
            limit=limit if limit is not None else self._settings.batch_size
        )
        sent = 0
        failed = 0
        for event in pending:
            if self._deliver(event):
                sent += 1
            else:
                failed += 1
        return OutboxRelayResult(
            sent_count=sent,
            checked_count=len(pending),
            failed_count=failed,
        )
