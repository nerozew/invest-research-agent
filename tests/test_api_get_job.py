"""P04-03 GET /v1/research-jobs/{id} 单元测试。

使用 fake in-memory JobQueryStore 与 fake HealthChecker，不连接真实数据库/Redis/Docker。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from conftest import db_integration_enabled
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from invest_research.api.app import create_app
from invest_research.application.jobs import (
    JobQueryStore,
    JobSnapshot,
    StepSnapshot,
)
from invest_research.domain.annual_pipeline import (
    NodeDependency,
    ResearchMode,
    ResearchNode,
    ResearchNodeKind,
    ResearchNodeStatus,
)
from invest_research.domain.status import JobStatus, StepStatus
from invest_research.infrastructure.db import annual_node_store
from invest_research.infrastructure.db.annual_node_store import SqlAnnualNodeStore
from invest_research.infrastructure.db.application_stores import SqlJobQueryStore
from invest_research.infrastructure.db.models import AnnualResearchNode, ResearchJob
from invest_research.settings import Settings


class FakeJobQueryStore:
    """内存版 JobQueryStore：按 job_id 返回预置快照，不存在返回 None。"""

    def __init__(self, snapshot: JobSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self.calls = 0

    def get(self, job_id: uuid.UUID) -> JobSnapshot | None:
        self.calls += 1
        if self._snapshot is None:
            return None
        # 断言调用方确实传入了我们构造时的 job_id
        if self._snapshot.job_id != job_id:
            return None
        return self._snapshot


class FakeChecker:
    """回归用的 fake checker（P04-01 模式）。"""

    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "llm_api_key": "test-key",
        "sec_user_agent_contact": "test@example.com",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client(store: JobQueryStore) -> TestClient:
    app = create_app(
        settings=_settings(),
        health_checker=FakeChecker(),
        job_query_store=store,
    )
    return TestClient(app)


def _snapshot(
    *,
    status: JobStatus = JobStatus.RUNNING,
) -> JobSnapshot:
    job_id = uuid.uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
    step = StepSnapshot.build(
        step_name="step01_resolve_company",
        sequence_no=1,
        status=StepStatus.SUCCEEDED,
        attempt_count=1,
        error_code=None,
        error_message=None,
        started_at=start,
        completed_at=start + timedelta(seconds=10),
    )
    return JobSnapshot.build(
        job_id=job_id,
        status=status,
        current_step="step02_run_research_agent",
        error_code="NETWORK_TRANSIENT" if status == JobStatus.FAILED else None,
        error_message="SEC timeout" if status == JobStatus.FAILED else None,
        started_at=start,
        completed_at=end if status.is_terminal else None,
        steps=(step,),
    )


def test_get_job_returns_200_with_status_and_steps() -> None:
    snap = _snapshot()
    client = _client(FakeJobQueryStore(snap))

    response = client.get(f"/v1/research-jobs/{snap.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(snap.job_id)
    assert body["status"] == "running"
    assert body["current_step"] == "step02_run_research_agent"
    assert len(body["steps"]) == 1
    step = body["steps"][0]
    assert step["step_name"] == "step01_resolve_company"
    assert step["sequence_no"] == 1
    assert step["status"] == "succeeded"
    assert step["attempt_count"] == 1
    assert step["duration_seconds"] == 10.0


def test_get_job_returns_error_and_duration_for_failed_job() -> None:
    snap = _snapshot(status=JobStatus.FAILED)
    client = _client(FakeJobQueryStore(snap))

    response = client.get(f"/v1/research-jobs/{snap.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "NETWORK_TRANSIENT"
    assert body["error_message"] == "SEC timeout"
    assert body["duration_seconds"] == 30.0
    assert body["completed_at"] is not None


def test_get_job_returns_duration_none_when_not_started() -> None:
    job_id = uuid.uuid4()
    snap = JobSnapshot(job_id=job_id, status=JobStatus.PENDING)
    client = _client(FakeJobQueryStore(snap))

    response = client.get(f"/v1/research-jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["started_at"] is None
    assert body["duration_seconds"] is None
    assert body["steps"] == []


def test_get_job_returns_404_when_not_found() -> None:
    client = _client(FakeJobQueryStore(None))

    response = client.get(f"/v1/research-jobs/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在"


def test_get_job_returns_422_for_invalid_uuid() -> None:
    client = _client(FakeJobQueryStore(None))

    response = client.get("/v1/research-jobs/not-a-uuid")

    assert response.status_code == 422


def test_get_job_returns_503_when_no_store_injected() -> None:
    app = create_app(settings=_settings(), health_checker=FakeChecker())
    client = TestClient(app)

    response = client.get(f"/v1/research-jobs/{uuid.uuid4()}")

    assert response.status_code == 503
    assert "任务查询存储未连接" in response.json()["detail"]


def test_health_and_create_still_work_after_adding_get_job() -> None:
    """回归：新增查询接口后 /health、/readiness、创建接口仍正常。"""
    store = FakeJobQueryStore(_snapshot())
    client = _client(store)

    assert client.get("/health").status_code == 200
    assert client.get("/readiness").status_code == 200
    resp = client.post(
        "/v1/research-jobs",
        json={
            "input_company": "Microsoft",
            "as_of_date": "2025-01-01",
            "language": "zh-CN",
            "requested_forms": ["10-K"],
        },
    )
    # 未注入 job_store，创建接口返回 503（保持模块导入零连接）
    assert resp.status_code == 503


def test_get_annual_job_serializes_node_snapshot_with_legacy_naive_timestamp(
    monkeypatch: pytest.MonkeyPatch,
    sql_session_factory: sessionmaker[Session],
) -> None:
    """年度详情不得因旧 SQLite/数据库时间失去时区而返回 500。"""
    monkeypatch.setattr(
        annual_node_store, "_utc_now", lambda: datetime(2026, 8, 27, 7, 0, 9, tzinfo=timezone.utc)
    )
    factory = sql_session_factory
    job_id = uuid.uuid4()
    with factory() as session:
        session.add(
            ResearchJob(
                id=job_id,
                input_company="Acme",
                as_of_date=date(2026, 8, 27),
                requested_forms=["10-K"],
                research_mode=ResearchMode.ANNUAL_DEEP.value,
            )
        )
        session.commit()

    node_store = SqlAnnualNodeStore(factory)
    node_store.create_graph(
        job_id=job_id,
        nodes=(
            ResearchNode(
                node_key="annual_fanout",
                kind=ResearchNodeKind.DOWNLOAD_FILING,
            ),
        ),
    )
    node_store.transition(
        job_id=job_id,
        node_key="annual_fanout",
        target=ResearchNodeStatus.RUNNING,
    )
    with factory() as session:
        row = session.execute(select(AnnualResearchNode)).scalar_one()
        # 模拟旧写入路径：没有 tzinfo 的值仍按 UTC 解释。
        row.started_at = datetime(2026, 8, 27, 7, 0, 0)
        session.commit()

    with _client(SqlJobQueryStore(factory)) as client:
        response = client.get(f"/v1/research-jobs/{job_id}")

    assert response.status_code == 200
    annual_nodes = response.json()["annual_nodes"]
    assert annual_nodes["critical_path_node_keys"] == ["annual_fanout"]
    assert annual_nodes["critical_path_seconds"] == 9.0


@pytest.mark.skipif(not db_integration_enabled(), reason="需要 PostgreSQL 集成测试")
@pytest.mark.parametrize("database_timezone", ["UTC", "Asia/Shanghai"])
def test_annual_job_detail_handles_postgres_aware_running_and_completed_nodes(
    pg_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    database_timezone: str,
) -> None:
    """PostgreSQL 非 UTC 会话返回偏移时间，任务详情仍保持精确耗时与 UTC 事件。"""
    now = datetime(2026, 8, 27, 7, 0, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(annual_node_store, "_utc_now", lambda: now)
    engine = create_engine(
        pg_session_factory.kw["bind"].url,
        connect_args={"options": f"-c timezone={database_timezone}"},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)
    try:
        job_id = uuid.uuid4()
        with factory() as session:
            session.add(
                ResearchJob(
                    id=job_id, input_company="Acme", as_of_date=date(2025, 10, 31),
                    research_mode="annual_deep", status="running",
                    started_at=now - timedelta(seconds=9),
                )
            )
            session.commit()
        store = SqlAnnualNodeStore(factory)
        store.create_graph(
            job_id=job_id,
            nodes=tuple(
                ResearchNode(node_key=key, kind=ResearchNodeKind.DOWNLOAD_FILING)
                for key in ("fetch", "write")
            ),
            dependencies=(NodeDependency(upstream_node_key="fetch", downstream_node_key="write"),),
        )
        for key, status in (
            ("fetch", ResearchNodeStatus.RUNNING),
            ("fetch", ResearchNodeStatus.SUCCEEDED),
            ("write", ResearchNodeStatus.RUNNING),
        ):
            store.transition(job_id=job_id, node_key=key, target=status)
        with factory() as session:
            for row in session.execute(select(AnnualResearchNode)).scalars():
                row.started_at = now - timedelta(seconds=9 if row.node_key == "fetch" else 6)
                row.completed_at = now - timedelta(seconds=6) if row.node_key == "fetch" else None
            session.commit()
        with factory() as session:
            row = session.execute(select(AnnualResearchNode).limit(1)).scalar_one()
            assert row.started_at is not None
            assert row.started_at.utcoffset() == timedelta(
                hours=8 if database_timezone == "Asia/Shanghai" else 0
            )
        with _client(SqlJobQueryStore(factory)) as client:
            for completed in (False, True):
                if completed:
                    store.transition(
                        job_id=job_id, node_key="write", target=ResearchNodeStatus.SUCCEEDED
                    )
                    with factory() as session:
                        job = session.get(ResearchJob, job_id)
                        assert job is not None
                        job.status = "succeeded"
                        job.completed_at = now
                        session.commit()
                response = client.get(f"/v1/research-jobs/{job_id}")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == ("succeeded" if completed else "running")
                assert body["research_mode"] == "annual_deep"
                assert body["annual_nodes"]["critical_path_node_keys"] == ["fetch", "write"]
                assert body["annual_nodes"]["critical_path_seconds"] == 9.0
                assert all(
                    datetime.fromisoformat(event["created_at"]).utcoffset() == timedelta(0)
                    for event in body["annual_nodes"]["recent_events"]
                )
                if completed:
                    assert body["duration_seconds"] == 9.0
    finally:
        engine.dispose()
