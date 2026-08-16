"""P05-03B Transactional Outbox 与 pending 投递恢复测试。

覆盖：
- SqlJobStore 创建 Job 与 Outbox 事件同事务写入（原子）；
- OutboxRelayService.publish 即时投递成功 → sent；
- dispatch 失败 → 回拨 pending + attempts+1（未达上限）；
- 达上限 → failed；
- relay_once 批量恢复未投递事件（重启恢复）；
- mark_claimed 并发防重复投递；
- 幂等复用不重复投递（已有 sent 事件不再投递）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Callable

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.application.outbox import (
    EVENT_TYPE_JOB_CREATED,
    OutboxRelayCounter,
    OutboxRelayService,
    OutboxSettings,
    OutboxStore,
)
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.db.application_stores import (
    SqlJobStore,
    SqlOutboxStore,
)
from invest_research.infrastructure.db.base import Base
from invest_research.infrastructure.db.models import OutboxEvent as OutboxEventORM
from invest_research.infrastructure.db.models import ResearchJob as ResearchJobORM
from invest_research.infrastructure.queue.outbox_relay import run_outbox_relay

Dispatcher = Callable[[uuid.UUID], None]


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="Microsoft",
        as_of_date="2025-01-01",
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
    )


@pytest.fixture()
def sf():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


def _event_status(sf, job_id: uuid.UUID) -> str | None:
    with sf() as session:
        row = session.execute(
            select(OutboxEventORM).where(OutboxEventORM.job_id == job_id)
        ).scalar_one_or_none()
        return row.status if row is not None else None


class _OkDispatcher:
    """JobDispatcher 兼容：dispatch 成功透传。"""

    def __init__(self) -> None:
        self.trace_contexts: list[Mapping[str, str] | None] = []

    def dispatch(
        self,
        job_id: uuid.UUID,
        *,
        trace_context: Mapping[str, str] | None = None,
    ) -> None:
        self.trace_contexts.append(trace_context)
        return None


class _FailingDispatcher:
    """JobDispatcher 兼容：dispatch 抛异常（模拟 broker 不可用）。"""

    def dispatch(
        self,
        job_id: uuid.UUID,
        *,
        trace_context: Mapping[str, str] | None = None,
    ) -> None:
        raise RuntimeError("broker down")


def _ok_dispatcher() -> _OkDispatcher:
    return _OkDispatcher()


def _failing_dispatcher() -> _FailingDispatcher:
    return _FailingDispatcher()


def test_job_create_writes_outbox_event_in_same_tx(sf) -> None:
    """SqlJobStore 创建 Job 时同事务写入 outbox 事件（原子）。"""
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)

    with sf() as session:
        assert session.get(ResearchJobORM, job_id) is not None
    assert _event_status(sf, job_id) == "pending"


def test_publish_success_marks_sent(sf) -> None:
    """publish 即时投递成功 → 事件标记为 sent。"""
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    outbox_store: OutboxStore = SqlOutboxStore(sf)
    service = OutboxRelayService(outbox_store, _ok_dispatcher())

    result = service.publish(job_id, EVENT_TYPE_JOB_CREATED)

    assert result.sent_count == 1
    assert result.failed_count == 0
    assert _event_status(sf, job_id) == "sent"


def test_outbox_relay_forwards_persisted_trace_context(sf) -> None:
    """A delayed relay must use context persisted with the outbox event."""
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    with sf() as session:
        row = session.execute(
            select(OutboxEventORM).where(OutboxEventORM.job_id == job_id)
        ).scalar_one()
        row.payload = {
            "job_id": str(job_id),
            "trace_context": {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
        }
        session.commit()

    dispatcher = _OkDispatcher()
    result = OutboxRelayService(SqlOutboxStore(sf), dispatcher).publish(job_id)

    assert result.sent_count == 1
    assert dispatcher.trace_contexts == [{"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}]


def test_publish_failure_requeues_until_max_attempts(sf) -> None:
    """dispatch 失败：未达上限回拨 pending + attempts+1；达上限转 failed。"""
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    outbox_store: OutboxStore = SqlOutboxStore(sf)
    service = OutboxRelayService(
        outbox_store, _failing_dispatcher(), OutboxSettings(max_attempts=2)
    )

    result = service.publish(job_id, EVENT_TYPE_JOB_CREATED)
    assert result.sent_count == 0
    assert result.failed_count == 1
    with sf() as session:
        row = session.execute(
            select(OutboxEventORM).where(OutboxEventORM.job_id == job_id)
        ).scalar_one()
        assert row.status == "pending"
        assert row.attempts == 1

    result2 = service.publish(job_id, EVENT_TYPE_JOB_CREATED)
    assert result2.sent_count == 0
    with sf() as session:
        row = session.execute(
            select(OutboxEventORM).where(OutboxEventORM.job_id == job_id)
        ).scalar_one()
        assert row.status == "failed"


def test_relay_once_recovers_pending_after_restart(sf) -> None:
    """relay_once 恢复此前未投递的 pending 事件（重启后恢复）。"""
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    outbox_store: OutboxStore = SqlOutboxStore(sf)
    service = OutboxRelayService(outbox_store, _ok_dispatcher())
    counter = OutboxRelayCounter()

    sent = run_outbox_relay(service, counter)

    assert sent == 1
    assert counter.total_sent == 1
    assert _event_status(sf, job_id) == "sent"
    # 再次 relay：已 sent 不重复投递
    assert run_outbox_relay(service, counter) == 0


def test_mark_claimed_prevents_double_dispatch(sf) -> None:
    """mark_claimed 条件更新防并发重复投递：已 claimed 的事件返回 False。"""
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    outbox_store: OutboxStore = SqlOutboxStore(sf)
    with sf() as session:
        row = session.execute(
            select(OutboxEventORM).where(OutboxEventORM.job_id == job_id)
        ).scalar_one()
        event_id = row.id

    assert outbox_store.mark_claimed(event_id) is True
    # 第二次领取失败（已被 claimed）→ 不重复投递
    assert outbox_store.mark_claimed(event_id) is False
