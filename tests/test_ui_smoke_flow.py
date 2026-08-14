"""P04-UI-06~10：连贯导航所需的端到端 smoke 测试（fake stores）。

用内存 fake 存储串联「创建 → 列表可见 → 详情 → 取消 → 无工件空态」，
验证前端用户路径依赖的后端契约全部可用，不依赖真实数据库/Redis/Docker。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.artifacts import ArtifactCatalogStore, ArtifactInfo
from invest_research.application.cancellation import CancelStatusWriter
from invest_research.application.job_listing import JobListCursor, JobListEntry, JobListStore
from invest_research.application.jobs import JobQueryStore, JobSnapshot, JobStore
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus
from invest_research.settings import Settings


class FakeChecker:
    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


class MemoryStore(JobStore, JobQueryStore, JobListStore, ArtifactCatalogStore, CancelStatusWriter):
    """内存实现 4 个端口，串联一份数据。"""

    def __init__(self) -> None:
        self._rows: dict[uuid.UUID, dict[str, object]] = {}
        self._artifacts: dict[uuid.UUID, list[ArtifactInfo]] = {}

    # JobStore
    def create(self, *, request: ResearchRequest, job_id: uuid.UUID) -> None:
        self._rows[job_id] = {
            "input_company": request.input_company,
            "as_of_date": request.as_of_date,
            "language": request.language,
            "status": JobStatus.PENDING,
            "created_at": datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc),
            "current_step": None,
            "error_code": None,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
        }

    # JobQueryStore
    def get(self, job_id: uuid.UUID) -> JobSnapshot | None:
        row = self._rows.get(job_id)
        if row is None:
            return None
        return JobSnapshot(job_id=job_id, status=row["status"])

    # JobListStore
    def list_jobs(
        self, *, status: JobStatus | None, limit: int, before: JobListCursor | None
    ) -> tuple[JobListEntry, ...]:
        entries = [
            JobListEntry(
                job_id=jid,
                input_company=str(r["input_company"]),
                as_of_date=r["as_of_date"],
                language=str(r["language"]),
                status=r["status"],
                current_step=r["current_step"],
                error_code=r["error_code"],
                created_at=r["created_at"],
                started_at=r["started_at"],
                completed_at=r["completed_at"],
            )
            for jid, r in self._rows.items()
        ]
        entries.sort(key=lambda e: (e.created_at, e.job_id), reverse=True)
        if before is not None:
            entries = [e for e in entries if (e.created_at, e.job_id) < before]
        if status is not None:
            entries = [e for e in entries if e.status == status]
        return tuple(entries[:limit])

    # CancelStatusWriter
    def cancel_from_pending(self, job_id: uuid.UUID) -> bool:
        return self._try_cancel(job_id, JobStatus.PENDING)

    def cancel_from_running(self, job_id: uuid.UUID) -> bool:
        return self._try_cancel(job_id, JobStatus.RUNNING)

    def _try_cancel(self, job_id: uuid.UUID, from_status: JobStatus) -> bool:
        row = self._rows.get(job_id)
        if row is None or row["status"] != from_status:
            return False
        row["status"] = JobStatus.CANCELLED
        return True

    # ArtifactCatalogStore
    def list_artifacts(self, job_id: uuid.UUID) -> tuple[ArtifactInfo, ...]:
        return tuple(self._artifacts.get(job_id, []))


def _settings() -> Settings:
    return Settings(llm_api_key="test-key", sec_user_agent_contact="test@example.com")


def _client(store: MemoryStore) -> TestClient:
    app = create_app(
        settings=_settings(),
        health_checker=FakeChecker(),
        job_store=store,
        job_query_store=store,
        job_list_store=store,
        cancel_status_writer=store,
        artifact_catalog_store=store,
    )
    return TestClient(app)


def test_ui_smoke_create_list_get_cancel_empty_artifacts() -> None:
    """用户路径：创建 → 列表可见 → 详情 → 取消 → 无工件空态。"""
    store = MemoryStore()
    client = _client(store)

    # 1. 创建任务
    create_resp = client.post(
        "/v1/research-jobs",
        json={
            "input_company": "Microsoft",
            "as_of_date": "2026-07-31",
            "language": "zh-CN",
            "requested_forms": ["10-K", "10-Q"],
        },
    )
    assert create_resp.status_code == 202
    job_id = create_resp.json()["job_id"]

    # 2. 最近任务列表能看到刚创建的任务
    list_resp = client.get("/v1/research-jobs")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert any(item["job_id"] == job_id for item in items)
    assert items[0]["job_id"] == job_id  # created_at 倒序最新在前

    # 3. 详情可见
    get_resp = client.get(f"/v1/research-jobs/{job_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "pending"

    # 4. 取消任务
    cancel_resp = client.delete(f"/v1/research-jobs/{job_id}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["did_cancel"] is True

    # 5. 取消后详情刷新为 cancelled
    after_cancel = client.get(f"/v1/research-jobs/{job_id}")
    assert after_cancel.status_code == 200
    assert after_cancel.json()["status"] == "cancelled"

    # 6. 无工件：空列表（页面显示"当前任务尚无可下载工件"，不报错）
    artifacts_resp = client.get(f"/v1/research-jobs/{job_id}/artifacts")
    assert artifacts_resp.status_code == 200
    assert artifacts_resp.json() == []
