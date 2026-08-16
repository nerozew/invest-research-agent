"""P05-11 fault injection: DB commit / connection failures.

Validate commit semantics for Job + Outbox transactional write:
- session.commit failure -> create raises, no Job row persisted (no false success);
- DB connection failure -> create raises, no rows persisted;
- Job and Outbox event are atomic: both or neither.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.application.outbox import EVENT_TYPE_JOB_CREATED
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.db.application_stores import SqlJobStore
from invest_research.infrastructure.db.base import Base
from invest_research.infrastructure.db.models import (
    OutboxEvent as OutboxEventORM,
)
from invest_research.infrastructure.db.models import (
    ResearchJob as ResearchJobORM,
)


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


def _count_jobs(sf) -> int:
    with sf() as session:
        return len(session.execute(select(ResearchJobORM)).scalars().all())


def _count_events(sf) -> int:
    with sf() as session:
        return len(session.execute(select(OutboxEventORM)).scalars().all())


def test_create_success_persists_job_and_outbox_atomically(sf) -> None:
    """Baseline: Job and Outbox event persist together on success."""
    store = SqlJobStore(sf)
    job_id = uuid.uuid4()
    store.create(request=_request(), job_id=job_id)

    assert _count_jobs(sf) == 1
    assert _count_events(sf) == 1
    with sf() as session:
        row = session.execute(
            select(OutboxEventORM).where(OutboxEventORM.job_id == job_id)
        ).scalar_one_or_none()
        assert row is not None
        assert row.event_type == EVENT_TYPE_JOB_CREATED
        assert row.status == "pending"


# ---- fault injection: session.commit fails ----


class _CommittingSessionRaises:
    """Session double with add() ok but commit() raising (disk/DB write failure)."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __enter__(self) -> "_CommittingSessionRaises":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def add(self, _: object) -> None:
        return None

    def flush(self) -> None:
        """Mirror SqlJobStore's explicit parent-row flush before commit."""
        return None

    def commit(self) -> None:
        raise self._exc

    def rollback(self) -> None:
        return None


class _FailCommitFactory:
    """SessionFactory double: returns session whose commit() always fails."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self) -> "_CommittingSessionRaises":
        return _CommittingSessionRaises(self._exc)


def test_commit_failure_raises_and_persists_nothing(sf) -> None:
    """session.commit failure -> create raises; no Job or Outbox row persisted."""
    store = SqlJobStore(_FailCommitFactory(RuntimeError("disk full on commit")))
    job_id = uuid.uuid4()

    with pytest.raises(RuntimeError):
        store.create(request=_request(), job_id=job_id)

    # atomic semantics: nothing persisted, no false success
    assert _count_jobs(sf) == 0
    assert _count_events(sf) == 0


def test_commit_failure_is_not_swallowed_as_success(sf) -> None:
    """Commit failure must propagate (fail-fast), never report succeeded."""
    store = SqlJobStore(_FailCommitFactory(OSError("flush failed")))
    job_id = uuid.uuid4()

    with pytest.raises(OSError):
        store.create(request=_request(), job_id=job_id)
    assert _count_jobs(sf) == 0


# ---- fault injection: DB connection failure ----


class _ConnectionLostFactory:
    """SessionFactory double: opening a session fails (DB connection down)."""

    def __call__(self) -> "_CommittingSessionRaises":
        raise RuntimeError("database connection lost")


def test_connection_failure_raises_and_persists_nothing(sf) -> None:
    """DB connection failure -> create raises; no rows persisted, no false success."""
    store = SqlJobStore(_ConnectionLostFactory())
    job_id = uuid.uuid4()

    with pytest.raises(RuntimeError):
        store.create(request=_request(), job_id=job_id)

    assert _count_jobs(sf) == 0
    assert _count_events(sf) == 0
