"""P04-10A：生产 wiring + API 真实 Store 集成验证。

不连接外部服务：使用 SQLite 内存库替换默认 engine（通过 monkeypatch
create_db_engine 返回内存 engine），并注入 fake checker 避免 Redis 依赖。

验证：
- create_production_app 组装出真实 Store 并注入 app.state；
- POST /v1/research-jobs 返回 202（真实 SQL 落库，含 PowerIdempotency）；
- GET job 返回 200；
- 同 key 幂等复用返回同 job_id（200），异请求 409。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import invest_research.infrastructure.wiring as wiring
from invest_research.infrastructure.db.base import Base


class FakeChecker:
    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


@pytest.fixture()
def prod(monkeypatch):
    # 替换 wiring.create_db_engine 为 SQLite 内存 engine
    def _memory_engine(url: str):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    monkeypatch.setattr(wiring, "create_db_engine", _memory_engine)
    monkeypatch.setattr(wiring, "build_health_checker", lambda settings: FakeChecker())
    monkeypatch.setattr(wiring, "get_settings", lambda: _settings())

    app, container = wiring.create_production_app()
    # 建表（内存库的空库）
    Base.metadata.create_all(container.engine)
    return app, container


def _settings():
    from invest_research.settings import Settings

    return Settings(
        llm_api_key="test-key",
        sec_user_agent_contact="test@example.com",
        broker_url="memory://",
        artifact_root="/tmp/artifacts-test",
    )


def _valid_body() -> dict[str, object]:
    return {
        "input_company": "Apple",
        "as_of_date": "2024-12-31",
        "language": "zh-CN",
        "requested_forms": ["10-K"],
    }


def test_production_app_creates_job_and_gets_it(prod):
    app, _ = prod
    client = TestClient(app)
    resp = client.post("/v1/research-jobs", json=_valid_body())
    assert resp.status_code == 202
    body = resp.json()
    job_id = body["job_id"]
    uuid.UUID(job_id)

    get = client.get(f"/v1/research-jobs/{job_id}")
    assert get.status_code == 200
    assert get.json()["status"] == "pending"


def test_production_idempotency_reuse_same_key(prod):
    app, _ = prod
    client = TestClient(app)
    headers = {"Idempotency-Key": "key-1"}
    r1 = client.post("/v1/research-jobs", json=_valid_body(), headers=headers)
    assert r1.status_code == 202
    r2 = client.post("/v1/research-jobs", json=_valid_body(), headers=headers)
    assert r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]


def test_production_idempotency_conflict_different_body(prod):
    app, _ = prod
    client = TestClient(app)
    headers = {"Idempotency-Key": "key-2"}
    r1 = client.post("/v1/research-jobs", json=_valid_body(), headers=headers)
    assert r1.status_code == 202
    r2 = client.post(
        "/v1/research-jobs",
        json={**_valid_body(), "input_company": "Microsoft"},
        headers=headers,
    )
    assert r2.status_code == 409


def test_production_cancel_pending(prod):
    app, _ = prod
    client = TestClient(app)
    r = client.post("/v1/research-jobs", json=_valid_body())
    job_id = r.json()["job_id"]
    cancel = client.delete(f"/v1/research-jobs/{job_id}")
    assert cancel.status_code == 200
    assert cancel.json()["did_cancel"] is True
    got = client.get(f"/v1/research-jobs/{job_id}")
    assert got.json()["status"] == "cancelled"
