"""P04-02 POST /v1/research-jobs 单元测试。

使用 fake in-memory JobStore 与 fake HealthChecker，不连接真实数据库/Redis/Docker。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.domain.models import ResearchRequest
from invest_research.settings import Settings


class FakeJobStore:
    """内存版 JobStore：记录创建的请求，不落库。"""

    def __init__(self) -> None:
        self.created: list[tuple[uuid.UUID, ResearchRequest]] = []
        self.calls = 0

    def create(self, *, request: ResearchRequest, job_id: uuid.UUID) -> None:
        self.calls += 1
        self.created.append((job_id, request))


class FakeChecker:
    """/health 回归用的 fake checker（P04-01 模式）。"""

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


def _client(store: FakeJobStore) -> tuple[TestClient, FakeJobStore]:
    app = create_app(
        settings=_settings(),
        health_checker=FakeChecker(),
        job_store=store,
    )
    return TestClient(app), store


def _valid_body() -> dict[str, object]:
    return {
        "input_company": "Microsoft",
        "as_of_date": "2025-01-01",
        "language": "zh-CN",
        "requested_forms": ["10-K", "10-Q"],
    }


def test_create_job_returns_202_with_job_id_and_pending_status() -> None:
    store = FakeJobStore()
    client, store_ref = _client(store)

    response = client.post("/v1/research-jobs", json=_valid_body())

    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    uuid.UUID(body["job_id"])  # job_id 必须是合法 UUID
    assert body["status"] == "pending"
    assert store_ref.calls == 1


def test_create_job_persists_request_with_generated_job_id() -> None:
    store = FakeJobStore()
    client, store_ref = _client(store)
    body = _valid_body()

    response = client.post("/v1/research-jobs", json=body)

    assert response.status_code == 202
    assert store_ref.calls == 1
    created_job_id, saved_request = store_ref.created[0]
    assert str(created_job_id) == response.json()["job_id"]
    # 持久化的请求与用户输入一致（含去空白后的公司名）
    assert saved_request.input_company == "Microsoft"
    assert saved_request.as_of_date.isoformat() == "2025-01-01"
    assert saved_request.language == "zh-CN"
    assert saved_request.requested_forms == ("10-K", "10-Q")


def test_create_job_rejects_future_as_of_date_with_422() -> None:
    client, _ = _client(FakeJobStore())

    response = client.post(
        "/v1/research-jobs",
        json={**_valid_body(), "as_of_date": "2999-01-01"},
    )

    assert response.status_code == 422


def test_create_job_rejects_blank_company_with_422() -> None:
    client, _ = _client(FakeJobStore())

    response = client.post(
        "/v1/research-jobs",
        json={**_valid_body(), "input_company": "   "},
    )

    assert response.status_code == 422


def test_create_job_rejects_invalid_language_with_422() -> None:
    client, _ = _client(FakeJobStore())

    response = client.post(
        "/v1/research-jobs",
        json={**_valid_body(), "language": "fr"},
    )

    assert response.status_code == 422


def test_create_job_rejects_empty_forms_with_422() -> None:
    client, _ = _client(FakeJobStore())

    response = client.post(
        "/v1/research-jobs",
        json={**_valid_body(), "requested_forms": []},
    )

    assert response.status_code == 422


def test_create_job_accepts_explicit_annual_deep_mode() -> None:
    """P07-10A：年度模式创建独立任务，绝不静默改写为 legacy。"""
    store = FakeJobStore()
    client, store_ref = _client(store)

    response = client.post(
        "/v1/research-jobs",
        json={**_valid_body(), "research_mode": "annual_deep"},
    )

    assert response.status_code == 202
    assert store_ref.calls == 1
    assert store_ref.created[0][1].research_mode.value == "annual_deep"


def test_create_job_returns_503_when_no_store_injected() -> None:
    """未注入 JobStore 时（模块导入零连接），创建任务接口返回 503。"""
    app = create_app(settings=_settings(), health_checker=FakeChecker())
    client = TestClient(app)

    response = client.post("/v1/research-jobs", json=_valid_body())

    assert response.status_code == 503
    assert "任务存储未连接" in response.json()["detail"]


def test_health_still_works_after_adding_create_job() -> None:
    """回归：新增创建任务接口后 /health 仍为纯 liveness。"""
    client, _ = _client(FakeJobStore())

    health = client.get("/health")
    readiness = client.get("/readiness")

    assert health.status_code == 200
    assert readiness.status_code == 200


def test_factory_remains_import_safe_without_db_connection() -> None:
    """application factory 可重复创建，且不注入 store 时不会创建任何存储。"""
    app_a = create_app(settings=_settings(), health_checker=FakeChecker())
    app_b = create_app(settings=_settings(), health_checker=FakeChecker())

    assert app_a is not app_b
    assert app_a.state.job_service is None
    assert app_b.state.job_service is None
