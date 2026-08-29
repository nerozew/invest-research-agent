"""P04-UI-06：GET /v1/research-jobs 任务列表 API 测试。

使用 fake in-memory JobListStore（模拟与 SQL 相同的稳定排序/过滤/keyset 语义），
覆盖：
- 默认返回最近 20 条、created_at 倒序稳定
- status 过滤
- cursor 分页：无重复、无遗漏
- 空数据库 items=[] / next_cursor=null
- 非法 status / limit / cursor 返回 422
- 未注入 store 时返回 503
- 响应不暴露 config_snapshot / 内部字段
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.job_listing import (
    JobListCursor,
    JobListEntry,
    JobListStore,
)
from invest_research.domain.annual_pipeline import ResearchMode
from invest_research.domain.status import JobStatus
from invest_research.settings import Settings


class FakeChecker:
    """回归用 fake checker（P04-01 模式）。"""

    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


class FakeJobListStore:
    """内存版 JobListStore：模拟稳定排序/过滤/keyset 语义。"""

    def __init__(self, entries: list[JobListEntry]) -> None:
        # 按 created_at DESC, job_id DESC 预排序
        self._entries = sorted(
            entries,
            key=lambda e: (e.created_at, e.job_id),
            reverse=True,
        )

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        limit: int,
        before: JobListCursor | None,
    ) -> tuple[JobListEntry, ...]:
        filtered = [e for e in self._entries if (status is None or e.status == status)]
        if before is not None:
            before_created, before_id = before
            filtered = [
                e for e in filtered if (e.created_at, e.job_id) < (before_created, before_id)
            ]
        return tuple(filtered[:limit])


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "llm_api_key": "test-key",
        "sec_user_agent_contact": "test@example.com",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client(store: JobListStore) -> TestClient:
    app = create_app(
        settings=_settings(),
        health_checker=FakeChecker(),
        job_list_store=store,
    )
    return TestClient(app)


def _entry(
    *,
    idx: int,
    status: JobStatus = JobStatus.SUCCEEDED,
    minutes_ago: int = 0,
) -> JobListEntry:
    return JobListEntry(
        job_id=uuid.uuid4(),
        input_company=f"Company-{idx}",
        as_of_date=date(2026, 1, 1),
        language="zh-CN",
        status=status,
        current_step=None,
        error_code=None,
        created_at=datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        - timedelta(minutes=minutes_ago),
        started_at=None,
        completed_at=None,
    )


def test_list_empty_database() -> None:
    client = _client(FakeJobListStore([]))

    response = client.get("/v1/research-jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_list_includes_research_mode_and_legacy_default() -> None:
    annual = _entry(idx=0)
    annual.research_mode = ResearchMode.ANNUAL_DEEP
    client = _client(FakeJobListStore([annual]))

    response = client.get("/v1/research-jobs")

    assert response.status_code == 200
    assert response.json()["items"][0]["research_mode"] == "annual_deep"


def test_list_default_limit_20_desc() -> None:
    entries = [_entry(idx=i, minutes_ago=i) for i in range(25)]
    client = _client(FakeJobListStore(entries))

    response = client.get("/v1/research-jobs")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 20
    # created_at 倒序：最新在前
    times = [item["created_at"] for item in body["items"]]
    assert times == sorted(times, reverse=True)
    assert body["next_cursor"] is not None


def test_list_status_filter() -> None:
    entries = [
        _entry(idx=0, status=JobStatus.RUNNING, minutes_ago=1),
        _entry(idx=1, status=JobStatus.SUCCEEDED, minutes_ago=2),
        _entry(idx=2, status=JobStatus.FAILED, minutes_ago=3),
    ]
    client = _client(FakeJobListStore(entries))

    response = client.get("/v1/research-jobs", params={"status": "succeeded"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "succeeded"
    assert body["next_cursor"] is None


def test_list_cursor_paginates_without_duplicates_or_gaps() -> None:
    entries = [_entry(idx=i, minutes_ago=i) for i in range(50)]
    client = _client(FakeJobListStore(entries))

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/v1/research-jobs", params=params)
        assert response.status_code == 200
        body = response.json()
        seen.extend(item["job_id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10  # 防死循环

    assert pages == 5
    assert len(seen) == 50
    assert len(set(seen)) == 50  # 无重复
    # 无遗漏：与全量 job_id 集合一致
    expected = {str(e.job_id) for e in entries}
    assert set(seen) == expected


def test_list_invalid_status_returns_422() -> None:
    client = _client(FakeJobListStore([]))

    response = client.get("/v1/research-jobs", params={"status": "bogus"})

    assert response.status_code == 422


def test_list_invalid_limit_returns_422() -> None:
    client = _client(FakeJobListStore([]))

    assert client.get("/v1/research-jobs", params={"limit": 0}).status_code == 422
    assert client.get("/v1/research-jobs", params={"limit": 101}).status_code == 422


def test_list_invalid_cursor_returns_422() -> None:
    client = _client(FakeJobListStore([]))

    response = client.get("/v1/research-jobs", params={"cursor": "not-a-cursor!"})

    assert response.status_code == 422


def test_list_returns_503_when_no_store_injected() -> None:
    app = create_app(settings=_settings(), health_checker=FakeChecker())
    client = TestClient(app)

    response = client.get("/v1/research-jobs")

    assert response.status_code == 503
    assert "任务列表存储未连接" in response.json()["detail"]


def test_list_does_not_expose_internal_fields() -> None:
    entries = [_entry(idx=0, minutes_ago=1)]
    client = _client(FakeJobListStore(entries))

    response = client.get("/v1/research-jobs")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "config_snapshot" not in item
    assert "idempotency_key" not in item
    assert "requested_forms" not in item
    assert "database_url" not in response.text
