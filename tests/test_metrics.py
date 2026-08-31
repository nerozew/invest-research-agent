"""P05-06 Prometheus 指标测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.infrastructure.observability.metrics import (
    quality_gate_failures_total,
    research_jobs_total,
    workflow_steps_total,
)


class _FakeChecker:
    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


def _settings() -> object:
    from invest_research.settings import Settings

    return Settings(llm_api_key="test", sec_user_agent_contact="t@e.com")


def test_metrics_endpoint_returns_prometheus_format() -> None:
    app = create_app(settings=_settings(), health_checker=_FakeChecker())
    client = TestClient(app)

    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert "research_jobs_total" in resp.text
    assert "workflow_steps_total" in resp.text
    assert "tool_calls_total" in resp.text
    assert "# HELP" in resp.text


def test_metric_labels_have_no_high_cardinality() -> None:
    # 验证指标定义不包含 job_id / URL / 公司名 label
    for metric in (
        research_jobs_total,
        workflow_steps_total,
        quality_gate_failures_total,
    ):
        for label in metric._labelnames:
            assert "job_id" not in label
            assert "url" not in label
            assert "company" not in label


def test_metric_inc_sets_value() -> None:
    from prometheus_client import REGISTRY

    research_jobs_total.labels(status="succeeded").inc()
    sample = REGISTRY.get_sample_value(
        "research_jobs_total", {"status": "succeeded"}
    )
    assert sample is not None and sample >= 1
