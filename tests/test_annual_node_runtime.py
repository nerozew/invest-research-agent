"""P07-10 年度节点持久化、恢复和可观测性回归。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from invest_research.domain.annual_node_runtime import (
    AnnualNodeEventType,
    AnnualNodeTransitionError,
)
from invest_research.domain.annual_pipeline import (
    NodeDependency,
    ResearchNode,
    ResearchNodeKind,
    ResearchNodeStatus,
)
from invest_research.infrastructure.db import annual_node_store
from invest_research.infrastructure.db.annual_node_store import (
    AnnualNodeGraphError,
    SqlAnnualNodeStore,
)
from invest_research.infrastructure.db.models import (
    AnnualNodeEvent,
    AnnualResearchNode,
    ResearchJob,
)
from invest_research.infrastructure.observability.annual_nodes import annual_node_span


@pytest.fixture
def store(sql_session_factory: sessionmaker[Session]) -> tuple[
    SqlAnnualNodeStore, sessionmaker[Session]
]:
    return SqlAnnualNodeStore(sql_session_factory), sql_session_factory


def _job(factory: sessionmaker[Session]) -> uuid.UUID:
    job_id = uuid.uuid4()
    with factory() as session:
        session.add(
            ResearchJob(
                id=job_id,
                input_company="Acme",
                as_of_date=date(2026, 8, 26),
                requested_forms=["10-K"],
            )
        )
        session.commit()
    return job_id


def _node(key: str, *, attempts: int = 2) -> ResearchNode:
    return ResearchNode(
        node_key=key,
        kind=ResearchNodeKind.DOWNLOAD_FILING,
        max_attempts=attempts,
    )


def test_create_graph_is_idempotent_and_events_are_append_only(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
) -> None:
    node_store, factory = store
    job_id = _job(factory)
    nodes = (_node("target"), _node("facts"), _node("compare"))
    edges = (
        NodeDependency(upstream_node_key="target", downstream_node_key="compare"),
        NodeDependency(upstream_node_key="facts", downstream_node_key="compare"),
    )

    first = node_store.create_graph(job_id=job_id, nodes=nodes, dependencies=edges)
    second = node_store.create_graph(job_id=job_id, nodes=nodes, dependencies=edges)

    assert len(first.nodes) == len(second.nodes) == 3
    with factory() as session:
        assert len(session.execute(select(AnnualResearchNode)).scalars().all()) == 3
        assert len(session.execute(select(AnnualNodeEvent)).scalars().all()) == 3


def test_rejects_cross_job_duplicate_self_and_cycle(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
) -> None:
    node_store, factory = store
    job_id = _job(factory)
    other_job_id = _job(factory)
    nodes = (_node("a"), _node("b"))
    node_store.create_graph(job_id=job_id, nodes=nodes)
    node_store.create_graph(job_id=other_job_id, nodes=nodes)

    with pytest.raises(AnnualNodeGraphError, match="同属"):
        node_store.add_dependency(
            job_id=job_id, upstream_node_key="a", downstream_node_key="missing"
        )
    with pytest.raises(ValueError, match="自身"):
        NodeDependency(upstream_node_key="a", downstream_node_key="a")
    node_store.add_dependency(job_id=job_id, upstream_node_key="a", downstream_node_key="b")
    with pytest.raises(AnnualNodeGraphError, match="存在环"):
        node_store.add_dependency(job_id=job_id, upstream_node_key="b", downstream_node_key="a")


def test_transitions_waiting_and_terminal_upstream_blocks_child(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
) -> None:
    node_store, factory = store
    job_id = _job(factory)
    node_store.create_graph(
        job_id=job_id,
        nodes=(_node("upstream"), _node("downstream")),
        dependencies=(
            NodeDependency(upstream_node_key="upstream", downstream_node_key="downstream"),
        ),
    )

    assert [node.node_key for node in node_store.runnable_nodes(job_id=job_id)] == ["upstream"]
    assert node_store.snapshot(job_id=job_id).waiting[0].reason_code == "upstream_pending"
    node_store.transition(job_id=job_id, node_key="upstream", target=ResearchNodeStatus.RUNNING)
    with pytest.raises(AnnualNodeTransitionError):
        node_store.transition(job_id=job_id, node_key="upstream", target=ResearchNodeStatus.PENDING)
    node_store.transition(
        job_id=job_id,
        node_key="upstream",
        target=ResearchNodeStatus.FAILED_TERMINAL,
        error_code="SEC_UNAVAILABLE",
    )
    snapshot = node_store.snapshot(job_id=job_id)
    by_key = {node.node_key: node for node in snapshot.nodes}
    assert by_key["downstream"].status is ResearchNodeStatus.BLOCKED
    assert "upstream_terminal_failure" in [event.reason_code for event in snapshot.recent_events]


def test_stale_recovery_respects_remaining_attempts_and_success_is_immutable(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_store, factory = store
    now = datetime(2026, 8, 27, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(annual_node_store, "_utc_now", lambda: now)
    job_id = _job(factory)
    node_store.create_graph(
        job_id=job_id,
        nodes=(
            _node("retry", attempts=2),
            _node("final", attempts=1),
            _node("fresh"),
            _node("boundary"),
            _node("succeeded"),
        ),
    )
    for key in ("retry", "final", "fresh", "boundary", "succeeded"):
        node_store.transition(job_id=job_id, node_key=key, target=ResearchNodeStatus.RUNNING)
    node_store.transition(job_id=job_id, node_key="succeeded", target=ResearchNodeStatus.SUCCEEDED)
    ages = {"retry": 601, "final": 601, "fresh": 599, "boundary": 600, "succeeded": 900}
    with factory() as session:
        for row in session.execute(select(AnnualResearchNode)).scalars():
            # 阈值为严格超过 10 分钟；旧记录缺失 tzinfo 时仍按 UTC 解释。
            row.started_at = (now - timedelta(seconds=ages[row.node_key])).replace(tzinfo=None)
        session.commit()

    result = {node.node_key: node for node in node_store.recover_stale()}
    assert set(result) == {"retry", "final"}
    assert result["retry"].status is ResearchNodeStatus.FAILED_RETRYABLE
    assert result["final"].status is ResearchNodeStatus.FAILED_TERMINAL
    snapshot = node_store.snapshot(job_id=job_id)
    by_key = {node.node_key: node for node in snapshot.nodes}
    assert by_key["fresh"].status is ResearchNodeStatus.RUNNING
    assert by_key["boundary"].status is ResearchNodeStatus.RUNNING
    assert by_key["succeeded"].status is ResearchNodeStatus.SUCCEEDED
    recovered_events = [
        event for event in snapshot.recent_events if event.event_type == "recovered"
    ]
    assert {event.node_key for event in recovered_events} == {"retry", "final"}
    assert all(event.reason_code == "STALE_NODE_TIMEOUT" for event in recovered_events)
    assert all(event.created_at == now for event in recovered_events)
    assert all(event.event_no == 4 for event in recovered_events)
    node_store.transition(job_id=job_id, node_key="retry", target=ResearchNodeStatus.RUNNING)
    node_store.transition(job_id=job_id, node_key="retry", target=ResearchNodeStatus.SUCCEEDED)
    before = node_store.snapshot(job_id=job_id)
    assert node_store.recover_stale() == ()
    assert node_store.snapshot(job_id=job_id) == before


def test_recovery_event_failure_rolls_back_status_and_all_events(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 flush 仍必须维持状态和事件的事务原子性。"""
    node_store, factory = store
    now = datetime(2026, 8, 27, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(annual_node_store, "_utc_now", lambda: now)
    job_id = _job(factory)
    node_store.create_graph(job_id=job_id, nodes=(_node("stale"),))
    node_store.transition(job_id=job_id, node_key="stale", target=ResearchNodeStatus.RUNNING)
    with factory() as session:
        row = session.execute(select(AnnualResearchNode)).scalar_one()
        row.started_at = now - timedelta(minutes=11)
        session.commit()
    before = node_store.snapshot(job_id=job_id)
    original_append = node_store._append_event

    def fail_recovered_event(*args, **kwargs):
        if kwargs["event_type"] is AnnualNodeEventType.RECOVERED:
            raise RuntimeError("injected event failure")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(node_store, "_append_event", fail_recovered_event)
    with pytest.raises(RuntimeError, match="injected event failure"):
        node_store.recover_stale()
    assert node_store.snapshot(job_id=job_id) == before


def test_diamond_critical_path_budget_reason_and_span_redaction(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
) -> None:
    node_store, factory = store
    job_id = _job(factory)
    nodes = tuple(_node(key) for key in ("a", "b", "c", "d"))
    node_store.create_graph(
        job_id=job_id,
        nodes=nodes,
        dependencies=(
            NodeDependency(upstream_node_key="a", downstream_node_key="b"),
            NodeDependency(upstream_node_key="a", downstream_node_key="c"),
            NodeDependency(upstream_node_key="b", downstream_node_key="d"),
            NodeDependency(upstream_node_key="c", downstream_node_key="d"),
        ),
    )
    for key in ("a", "b", "c", "d"):
        node_store.transition(job_id=job_id, node_key=key, target=ResearchNodeStatus.RUNNING)
        node_store.transition(job_id=job_id, node_key=key, target=ResearchNodeStatus.SUCCEEDED)
    started = datetime.now() - timedelta(seconds=20)
    with factory() as session:
        durations = {"a": 1, "b": 5, "c": 2, "d": 3}
        for row in session.execute(select(AnnualResearchNode)).scalars():
            row.started_at = started
            row.completed_at = started + timedelta(seconds=durations[row.node_key])
        session.commit()
    node_store.record_budget_stop(job_id=job_id, node_key="a", reason_code="no_evidence_gain")
    snapshot = node_store.snapshot(job_id=job_id)
    assert snapshot.critical_path_node_keys[0] == "a"
    assert snapshot.critical_path_node_keys[-1] == "d"
    assert snapshot.critical_path_seconds == 9
    assert snapshot.budget_stop_reasons == ("no_evidence_gain",)

    with annual_node_span(
        operation="recover",
        node_kind="download_filing",
        status="failed_retryable",
        attempt_count=1,
        error_code="STALE_NODE_TIMEOUT",
    ):
        pass


@pytest.mark.parametrize(
    ("start", "end", "expected_seconds"),
    [
        ("2026-08-27T07:00:00", "2026-08-27T07:00:09", 9.0),
        ("2026-08-27T07:00:00+00:00", "2026-08-27T07:00:09+00:00", 9.0),
        ("2026-08-27T07:00:00", "2026-08-27T07:00:09+00:00", 9.0),
        ("2026-08-27T07:00:00+00:00", "2026-08-27T07:00:09", 9.0),
        ("2026-08-27T15:00:00+08:00", "2026-08-27T03:00:09-04:00", 9.0),
        ("2026-08-27T07:00:00", "2026-08-27T15:00:09+08:00", 9.0),
        ("2026-08-27T07:00:00", None, 9.0),
        ("2026-08-27T07:00:00+00:00", None, 9.0),
        ("2026-08-27T15:00:00+08:00", None, 9.0),
        ("2026-08-27T07:00:10+00:00", None, 0.0),
        (None, None, 0.0),
    ],
    ids=[
        "naive", "utc", "naive-start", "naive-end", "offsets", "mixed-offset",
        "running-naive", "running-utc", "running-offset", "future-start", "not-started",
    ],
)
def test_critical_path_normalizes_naive_and_aware_database_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    start: str | None,
    end: str | None,
    expected_seconds: float,
) -> None:
    """直接保留 ORM 字段的时区，避免 SQLite 落盘去掉 tzinfo 掩盖混合时间错误。"""
    monkeypatch.setattr(
        annual_node_store, "_utc_now", lambda: datetime(2026, 8, 27, 7, 0, 9, tzinfo=timezone.utc)
    )
    row = AnnualResearchNode(
        node_key="mixed",
        started_at=datetime.fromisoformat(start) if start else None,
        completed_at=datetime.fromisoformat(end) if end else None,
    )

    path, seconds = SqlAnnualNodeStore._critical_path({"mixed": row}, ())

    assert path == ("mixed",)
    assert seconds == expected_seconds


def test_running_node_with_naive_timestamp_has_exact_critical_path(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        annual_node_store, "_utc_now", lambda: datetime(2026, 8, 27, 7, 0, 9, tzinfo=timezone.utc)
    )
    node_store, factory = store
    job_id = _job(factory)
    node_store.create_graph(job_id=job_id, nodes=(_node("running"),))
    node_store.transition(job_id=job_id, node_key="running", target=ResearchNodeStatus.RUNNING)
    with factory() as session:
        row = session.execute(select(AnnualResearchNode)).scalar_one()
        row.started_at = datetime(2026, 8, 27, 7, 0, 0)
        session.commit()

    snapshot = node_store.snapshot(job_id=job_id)

    assert snapshot.critical_path_node_keys == ("running",)
    assert snapshot.critical_path_seconds == 9.0


def test_early_budget_stop_reason_survives_later_event_window(
    store: tuple[SqlAnnualNodeStore, sessionmaker[Session]],
) -> None:
    """早期停止原因不能被 55 条后续事件挤出快照（_snapshot 之前只取最近 50 条事件）。"""
    node_store, factory = store
    job_id = _job(factory)
    node_store.create_graph(job_id=job_id, nodes=(_node("compare"),))
    node_store.record_budget_stop(
        job_id=job_id, node_key="compare", reason_code="supplement_already_attempted"
    )
    with factory() as session:
        node = (
            session.execute(select(AnnualResearchNode).where(AnnualResearchNode.job_id == job_id))
            .scalars()
            .one()
        )
        early = (
            session.execute(
                select(AnnualNodeEvent).where(
                    AnnualNodeEvent.node_id == node.id,
                    AnnualNodeEvent.event_type == AnnualNodeEventType.BUDGET_STOP.value,
                )
            )
            .scalars()
            .one()
        )
        base = early.created_at
        for i in range(55):
            session.add(
                AnnualNodeEvent(
                    job_id=job_id,
                    node_id=node.id,
                    event_no=1000 + i,
                    event_type=AnnualNodeEventType.STATE_CHANGED.value,
                    previous_status=ResearchNodeStatus.PENDING.value,
                    new_status=ResearchNodeStatus.RUNNING.value,
                    payload={},
                    created_at=base + timedelta(seconds=i + 1),
                )
            )
        session.commit()

    snapshot = node_store.snapshot(job_id=job_id)
    assert "supplement_already_attempted" in snapshot.budget_stop_reasons
