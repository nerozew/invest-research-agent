"""P04-01 API health/readiness 单元测试。

全部使用 fake checker，不启动 Docker、不连接真实 PostgreSQL/Redis。
"""

from __future__ import annotations

from typing import Literal

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.api.health import DependencyStatus, HealthResponse, ReadinessResponse
from invest_research.settings import Settings

# 复刻 health.py 内部但测试需要可见的类型别名。
_CheckStatus = Literal["ok", "unavailable"]
_CheckError = Literal["connect_timeout", "connection_error", "unknown"]


class FakeChecker:
    """记录调用并对每个依赖返回可配置状态的 fake checker。"""

    def __init__(
        self,
        *,
        db_status: _CheckStatus = "ok",
        redis_status: _CheckStatus = "ok",
    ) -> None:
        self.db_status = db_status
        self.redis_status = redis_status
        self.db_calls = 0
        self.redis_calls = 0

    def check_database(self) -> dict[str, _CheckStatus | _CheckError | None]:
        self.db_calls += 1
        return self._result(self.db_status)

    def check_redis(self) -> dict[str, _CheckStatus | _CheckError | None]:
        self.redis_calls += 1
        return self._result(self.redis_status)

    def _result(self, status: _CheckStatus) -> dict[str, _CheckStatus | _CheckError | None]:
        error: _CheckError | None = None if status == "ok" else "connection_error"
        return {"status": status, "error_code": error}


def _settings(**overrides: object) -> Settings:
    """构造最小可用的 Settings（不读真实 .env，避免依赖 LLM key）。"""
    defaults: dict[str, object] = {
        "llm_api_key": "test-key",
        "sec_user_agent_contact": "test@example.com",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client(
    checker: FakeChecker,
    *,
    db_key: str = "postgresql+psycopg://u:p@localhost:5432/db",
    redis_uri: str = "redis://localhost:6379/0",
) -> tuple[TestClient, FakeChecker]:
    app = create_app(
        settings=_settings(database_url=db_key, redis_url=redis_uri),
        health_checker=checker,
    )
    return TestClient(app), checker


def test_health_returns_200_without_touching_dependencies() -> None:
    checker = FakeChecker()
    client, checker_ref = _client(checker)

    response = client.get("/health")

    assert response.status_code == 200
    body = HealthResponse(**response.json())
    assert body.status == "ok"
    assert body.service == "invest-research"
    # /health 是纯 liveness：绝不调用 DB/Redis checker。
    assert checker_ref.db_calls == 0
    assert checker_ref.redis_calls == 0


def test_readiness_returns_200_when_all_dependencies_ok() -> None:
    client, _ = _client(FakeChecker(db_status="ok", redis_status="ok"))

    response = client.get("/readiness")

    assert response.status_code == 200
    body = ReadinessResponse(**response.json())
    assert body.status == "ready"
    assert body.ready is True
    assert body.database == DependencyStatus(status="ok")
    assert body.redis == DependencyStatus(status="ok")


def test_readiness_returns_503_when_database_down_and_reports_db_only() -> None:
    client, _ = _client(FakeChecker(db_status="unavailable", redis_status="ok"))

    response = client.get("/readiness")

    assert response.status_code == 503
    body = ReadinessResponse(**response.json())
    assert body.status == "not_ready"
    assert body.ready is False
    assert body.database == DependencyStatus(status="unavailable", error_code="connection_error")
    assert body.redis == DependencyStatus(status="ok")


def test_readiness_returns_503_when_redis_down_and_reports_redis_only() -> None:
    client, _ = _client(FakeChecker(db_status="ok", redis_status="unavailable"))

    response = client.get("/readiness")

    assert response.status_code == 503
    body = ReadinessResponse(**response.json())
    assert body.status == "not_ready"
    assert body.ready is False
    assert body.database == DependencyStatus(status="ok")
    assert body.redis == DependencyStatus(status="unavailable", error_code="connection_error")


def test_readiness_reports_both_when_both_down() -> None:
    client, _ = _client(FakeChecker(db_status="unavailable", redis_status="unavailable"))

    response = client.get("/readiness")

    assert response.status_code == 503
    body = ReadinessResponse(**response.json())
    assert body.status == "not_ready"
    assert body.ready is False
    assert body.database.status == "unavailable"
    assert body.redis.status == "unavailable"


def test_readiness_and_health_do_not_leak_connection_strings_or_keys() -> None:
    """响应（含错误路径）不得包含连接串、密码或密钥。"""
    checker = FakeChecker(db_status="unavailable", redis_status="unavailable")
    secrets = ["supersecret", "redissecret", "sk-leak-check", "10.0.0.5", "10.0.0.6"]
    client, _ = _client(
        checker,
        db_key="postgresql+psycopg://invest:supersecret@10.0.0.5:5432/invest",
        redis_uri="redis://:redissecret@10.0.0.6:6379/0",
    )

    readiness = client.get("/readiness")
    health = client.get("/health")

    assert readiness.status_code == 503
    for endpoint_response in (readiness, health):
        for secret in secrets:
            assert secret not in endpoint_response.text


def test_application_factory_creates_independent_apps() -> None:
    """application factory 可重复创建 app，测试之间不共享脏状态。"""
    checker_a = FakeChecker(db_status="ok", redis_status="ok")
    checker_b = FakeChecker(db_status="unavailable", redis_status="ok")

    app_a = create_app(settings=_settings(), health_checker=checker_a)
    app_b = create_app(settings=_settings(), health_checker=checker_b)

    assert app_a is not app_b
    assert app_a.state.health_checker is checker_a
    assert app_b.state.health_checker is checker_b

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/readiness").status_code == 200
        assert client_b.get("/readiness").status_code == 503
