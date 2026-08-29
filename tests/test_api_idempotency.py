"""P04-05 Idempotency-Key 幂等创建任务单元测试。

使用 fake job store + fake idempotency store，不连接真实数据库/Redis/Docker。
验收：同 key 同请求复用（200），不同请求 409。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.idempotency import IdempotencyStore, StoredJob
from invest_research.domain.models import ResearchRequest
from invest_research.settings import Settings


class FakeJobStore:
    """内存版 JobStore：记录创建次数。"""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, *, request: ResearchRequest, job_id: uuid.UUID) -> None:
        self.calls += 1


class FakeIdempotencyStore:
    """内存版 IdempotencyStore。"""

    def __init__(self) -> None:
        self._data: dict[str, StoredJob] = {}

    def get(self, key: str) -> StoredJob | None:
        return self._data.get(key)

    def save(self, key: str, job: StoredJob) -> None:
        self._data[key] = job


class FakeChecker:
    """回归用 fake checker（P04-01 模式）。"""

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


def _client(
    idempotency_store: IdempotencyStore | None = None,
) -> tuple[TestClient, FakeJobStore]:
    job_store = FakeJobStore()
    app = create_app(
        settings=_settings(),
        health_checker=FakeChecker(),
        job_store=job_store,
        idempotency_store=idempotency_store,
    )
    return TestClient(app), job_store


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "input_company": "Microsoft",
        "as_of_date": "2025-01-01",
        "language": "zh-CN",
        "requested_forms": ["10-K", "10-Q"],
    }
    payload.update(overrides)
    return payload


def test_create_without_key_still_returns_202() -> None:
    client, job_store = _client(idempotency_store=FakeIdempotencyStore())

    resp = client.post("/v1/research-jobs", json=_body())

    assert resp.status_code == 202
    assert job_store.calls == 1


def test_same_key_same_request_reuses_job() -> None:
    client, job_store = _client(idempotency_store=FakeIdempotencyStore())

    first = client.post("/v1/research-jobs", json=_body(), headers={"Idempotency-Key": "key-1"})
    second = client.post("/v1/research-jobs", json=_body(), headers={"Idempotency-Key": "key-1"})

    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    assert job_store.calls == 1


def test_same_key_different_request_conflicts_409() -> None:
    client, job_store = _client(idempotency_store=FakeIdempotencyStore())

    first = client.post("/v1/research-jobs", json=_body(), headers={"Idempotency-Key": "key-2"})
    second = client.post(
        "/v1/research-jobs",
        json=_body(input_company="Apple"),
        headers={"Idempotency-Key": "key-2"},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert job_store.calls == 1


def test_key_without_idempotency_store_falls_back_to_normal() -> None:
    client, job_store = _client(idempotency_store=None)

    resp = client.post("/v1/research-jobs", json=_body(), headers={"Idempotency-Key": "key-3"})

    assert resp.status_code == 202
    assert job_store.calls == 1


def test_annual_deep_uses_an_isolated_idempotency_fingerprint() -> None:
    """年度模式可创建，且其显式模式字段参与幂等隔离。"""
    client, job_store = _client(idempotency_store=FakeIdempotencyStore())

    response = client.post(
        "/v1/research-jobs",
        json=_body(research_mode="annual_deep"),
        headers={"Idempotency-Key": "annual-deep-key"},
    )

    assert response.status_code == 202
    assert job_store.calls == 1


def test_annual_deep_conflicts_with_legacy_when_reusing_the_same_key() -> None:
    """legacy 与 annual_deep 的同名幂等键不得交叉复用。"""
    client, job_store = _client(idempotency_store=FakeIdempotencyStore())
    headers = {"Idempotency-Key": "shared-key"}

    legacy = client.post("/v1/research-jobs", json=_body(), headers=headers)
    annual = client.post(
        "/v1/research-jobs",
        json=_body(research_mode="annual_deep"),
        headers=headers,
    )

    assert legacy.status_code == 202
    assert annual.status_code == 409
    assert job_store.calls == 1
