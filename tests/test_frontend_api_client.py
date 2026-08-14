"""P04-UI-01：typed API client 的 fake HTTP 测试。

使用 ``httpx.MockTransport`` 模拟后端响应，全程离线：
不依赖真实数据库、Redis、Docker 或模型（对齐架构文档 §11.4）。
覆盖：health/readiness 解析、创建任务携带 Idempotency-Key、
4xx/5xx/网络/超时错误分类、非法构造参数。
"""

from __future__ import annotations

import uuid
from datetime import date

import httpx
import pytest

from invest_research.domain.models import ResearchRequest
from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.errors import (
    ApiNetworkError,
    ApiNotFoundError,
    ApiTimeoutError,
    HttpStatusError,
)
from invest_research.frontend.models import (
    CancelJobResponse,
    CreateResearchJobRequest,
    CreateResearchJobResponse,
    HealthResponse,
    JobListPage,
    JobSnapshot,
    ReadinessResponse,
)

API_BASE = "http://api.test"


def _make_client(handler: httpx.Request) -> ResearchApiClient:
    """构造注入 MockTransport 的 client。"""
    transport = httpx.MockTransport(handler)
    return ResearchApiClient(base_url=API_BASE, timeout=5.0, transport=transport)


def _json_response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


# ---------------------------------------------------------------------------
# health / readiness
# ---------------------------------------------------------------------------


def test_health_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return _json_response(200, {"status": "ok", "service": "invest-research"})

    client = _make_client(handler)
    result = client.health()

    assert isinstance(result, HealthResponse)
    assert result.status == "ok"
    assert result.service == "invest-research"


def test_readiness_ready_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/readiness"
        return _json_response(
            200,
            {
                "status": "ready",
                "ready": True,
                "database": {"status": "ok", "error_code": None},
                "redis": {"status": "ok", "error_code": None},
            },
        )

    client = _make_client(handler)
    result = client.readiness()

    assert isinstance(result, ReadinessResponse)
    assert result.ready is True
    assert result.database.status == "ok"


def test_readiness_not_ready_503_is_not_an_error() -> None:
    """后端依赖不可用返回 503，但响应体仍是合法 ReadinessResponse（ready=False）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            503,
            {
                "status": "not_ready",
                "ready": False,
                "database": {"status": "unavailable", "error_code": "connect_timeout"},
                "redis": {"status": "ok", "error_code": None},
            },
        )

    client = _make_client(handler)
    result = client.readiness()

    assert result.ready is False
    assert result.status == "not_ready"
    assert result.database.status == "unavailable"


# ---------------------------------------------------------------------------
# 创建任务 + Idempotency-Key
# ---------------------------------------------------------------------------


def _sample_create_request() -> CreateResearchJobRequest:
    return ResearchRequest(
        input_company="Microsoft",
        as_of_date=date(2026, 7, 31),
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
    )


def test_create_research_job_sends_idempotency_key_and_parses_202() -> None:
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/research-jobs"
        assert request.method == "POST"
        assert request.headers["Idempotency-Key"] == "client-key-001"
        body = request.read().decode("utf-8")
        assert "Microsoft" in body
        return _json_response(202, {"job_id": str(job_id), "status": "pending"})

    client = _make_client(handler)
    result = client.create_research_job(
        request=_sample_create_request(), idempotency_key="client-key-001"
    )

    assert isinstance(result, CreateResearchJobResponse)
    assert result.job_id == job_id
    assert result.status == "pending"


def test_create_research_job_409_raises_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(409, {"detail": "Idempotency-Key 已被不同的请求体使用，禁止复用"})

    client = _make_client(handler)
    with pytest.raises(HttpStatusError) as exc_info:
        client.create_research_job(
            request=_sample_create_request(), idempotency_key="client-key-001"
        )

    assert exc_info.value.status_code == 409
    assert "禁止复用" in exc_info.value.detail
    assert exc_info.value.is_client_error is True
    assert exc_info.value.is_server_error is False


def test_create_research_job_rejects_empty_idempotency_key() -> None:
    client = _make_client(httpx.Response(202, json={}))
    with pytest.raises(ValueError, match="idempotency_key"):
        client.create_research_job(request=_sample_create_request(), idempotency_key="  ")


# ---------------------------------------------------------------------------
# 查询任务状态
# ---------------------------------------------------------------------------


def test_get_research_job_parses_snapshot() -> None:
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/research-jobs/{job_id}"
        return _json_response(
            200,
            {
                "job_id": str(job_id),
                "status": "running",
                "current_step": "03_research",
                "error_code": None,
                "error_message": None,
                "started_at": "2026-08-13T09:00:00",
                "completed_at": None,
                "duration_seconds": 12.5,
                "steps": [
                    {
                        "step_name": "00_request_validation",
                        "sequence_no": 0,
                        "status": "succeeded",
                        "attempt_count": 1,
                        "error_code": None,
                        "error_message": None,
                        "duration_seconds": 0.2,
                    }
                ],
            },
        )

    client = _make_client(handler)
    result = client.get_research_job(str(job_id))

    assert isinstance(result, JobSnapshot)
    assert result.status == "running"
    assert result.current_step == "03_research"
    assert len(result.steps) == 1
    assert result.steps[0].step_name == "00_request_validation"


def test_get_research_job_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"detail": "任务不存在"})

    client = _make_client(handler)
    with pytest.raises(ApiNotFoundError) as exc_info:
        client.get_research_job(str(uuid.uuid4()))

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 5xx / 网络 / 超时
# ---------------------------------------------------------------------------


def test_5xx_raises_http_status_error_with_server_flag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(503, {"detail": "任务存储未连接，无法创建任务"})

    client = _make_client(handler)
    with pytest.raises(HttpStatusError) as exc_info:
        client.create_research_job(request=_sample_create_request(), idempotency_key="key-503")

    assert exc_info.value.status_code == 503
    assert exc_info.value.is_server_error is True


def test_network_error_raises_api_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    with pytest.raises(ApiNetworkError):
        client.health()


def test_timeout_raises_api_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    client = _make_client(handler)
    with pytest.raises(ApiTimeoutError):
        client.health()


# ---------------------------------------------------------------------------
# 构造参数
# ---------------------------------------------------------------------------


def test_client_rejects_empty_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        ResearchApiClient(base_url="   ")


# ---------------------------------------------------------------------------
# 工件清单与下载（P04-UI-04）
# ---------------------------------------------------------------------------


def test_list_artifacts_parses_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = "/v1/research-jobs/00000000-0000-0000-0000-000000000001/artifacts"
        assert request.url.path == path
        return _json_response(
            200,
            [
                {
                    "artifact_key": "report.md",
                    "artifact_type": "report",
                    "schema_version": None,
                    "storage_uri": "artifacts/job/report.md",
                    "content_checksum": "abc123",
                    "byte_size": 123,
                }
            ],
        )

    client = _make_client(handler)
    artifacts = client.list_artifacts("00000000-0000-0000-0000-000000000001")

    assert len(artifacts) == 1
    assert artifacts[0].artifact_key == "report.md"
    assert artifacts[0].byte_size == 123


def test_download_artifact_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = "/v1/research-jobs/00000000-0000-0000-0000-000000000001/artifacts/report.md"
        assert request.url.path == path
        return httpx.Response(200, content=b"# Report")

    client = _make_client(handler)
    content = client.download_artifact("00000000-0000-0000-0000-000000000001", "report.md")

    assert content == b"# Report"


def test_download_artifact_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"detail": "工件不存在"})

    client = _make_client(handler)
    with pytest.raises(ApiNotFoundError):
        client.download_artifact("00000000-0000-0000-0000-000000000001", "missing.md")


# ---------------------------------------------------------------------------
# 任务列表（P04-UI-06）
# ---------------------------------------------------------------------------


def test_list_jobs_parses_page_and_sends_query_params() -> None:
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/research-jobs"
        assert request.method == "GET"
        assert request.url.params["limit"] == "5"
        assert request.url.params["status"] == "running"
        return _json_response(
            200,
            {
                "items": [
                    {
                        "job_id": str(job_id),
                        "input_company": "Microsoft",
                        "as_of_date": "2026-07-31",
                        "language": "zh-CN",
                        "status": "running",
                        "current_step": "03_research",
                        "error_code": None,
                        "created_at": "2026-08-14T09:00:00",
                        "started_at": "2026-08-14T09:00:00",
                        "completed_at": None,
                    }
                ],
                "next_cursor": "abc123",
            },
        )

    client = _make_client(handler)
    page = client.list_jobs(limit=5, status="running", cursor="xyz")

    assert isinstance(page, JobListPage)
    assert len(page.items) == 1
    assert page.items[0].input_company == "Microsoft"
    assert page.items[0].status.value == "running"
    assert page.next_cursor == "abc123"


def test_list_jobs_empty_page_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"items": [], "next_cursor": None})

    client = _make_client(handler)
    page = client.list_jobs()

    assert page.items == ()
    assert page.next_cursor is None


def test_list_jobs_503_raises_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(503, {"detail": "任务列表存储未连接，无法列出任务"})

    client = _make_client(handler)
    with pytest.raises(HttpStatusError) as exc_info:
        client.list_jobs()

    assert exc_info.value.status_code == 503
    assert exc_info.value.is_server_error is True


# ---------------------------------------------------------------------------
# 取消任务（P04-08 / P04-UI-10）
# ---------------------------------------------------------------------------


def test_cancel_research_job_parses_response() -> None:
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/research-jobs/{job_id}"
        assert request.method == "DELETE"
        return _json_response(
            200,
            {
                "job_id": str(job_id),
                "status": "cancelled",
                "did_cancel": True,
                "already_cancelled": None,
            },
        )

    client = _make_client(handler)
    result = client.cancel_research_job(str(job_id))

    assert isinstance(result, CancelJobResponse)
    assert result.did_cancel is True
    assert result.status.value == "cancelled"


def test_cancel_research_job_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"detail": "任务不存在"})

    client = _make_client(handler)
    with pytest.raises(ApiNotFoundError):
        client.cancel_research_job(str(uuid.uuid4()))
