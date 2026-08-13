"""P04-03 GET /v1/research-jobs/{id} 单元测试。

使用 fake in-memory JobQueryStore 与 fake HealthChecker，不连接真实数据库/Redis/Docker。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.jobs import (
    JobQueryStore,
    JobSnapshot,
    StepSnapshot,
)
from invest_research.domain.status import JobStatus, StepStatus
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
