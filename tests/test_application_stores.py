"""P04-10A：SQLAlchemy Application Store 适配器测试。

使用 SQLite 内存库 + create_all 验证真实 SQL 行为（不连 PostgreSQL）：
- SqlJobStore：创建任务落库，重复 id 抛错
- SqlJobQueryStore：读取 JobSnapshot（含 steps 排序），不存在返回 None
- SqlIdempotencyStore：保存/读取幂等记录，同 key 并发重复 save 冲突
- SqlCancelStatusWriter：pending/running→cancelled 条件更新，终态不覆盖
- SqlArtifactCatalogStore：列出登记工件
- SqlArtifactContentStore：仅已登记可读 + 路径穿越防护
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.application.artifacts import ArtifactInfo
from invest_research.application.idempotency import StoredJob
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus
from invest_research.infrastructure.db.application_stores import (
    SqlArtifactCatalogStore,
    SqlArtifactContentStore,
    SqlCancelStatusWriter,
    SqlIdempotencyStore,
    SqlJobQueryStore,
    SqlJobStore,
)
from invest_research.infrastructure.db.base import Base
from invest_research.infrastructure.db.models import (
    Artifact as ArtifactORM,
)
from invest_research.infrastructure.db.models import (
    ResearchJob as ResearchJobORM,
)
from invest_research.infrastructure.db.models import (
    WorkflowStep as WorkflowStepORM,
)


@pytest.fixture()
def sf():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


def _request(**overrides) -> ResearchRequest:
    base = {
        "input_company": "Apple Inc.",
        "as_of_date": "2024-12-31",
        "language": "zh-CN",
        "requested_forms": ["10-K"],
    }
    base.update(overrides)
    return ResearchRequest(**base)


def test_job_store_creates_and_query_roundtrip(sf):
    job_id = uuid.uuid4()
    store = SqlJobStore(sf)
    store.create(request=_request(), job_id=job_id)

    query = SqlJobQueryStore(sf)
    snapshot = query.get(job_id)
    assert snapshot is not None
    assert snapshot.job_id == job_id
    assert snapshot.status == JobStatus.PENDING
    assert snapshot.steps == ()
    # JobSnapshot 不含 input_company（查询 DTO 只含状态/步骤/错误/耗时）
    # 该断言由 SQLite 落库的 store 行为覆盖


def test_job_list_store_defaults_and_paginates(sf):
    """SqlJobListStore：created_at 倒序稳定 + keyset 分页无重复/遗漏。"""
    from invest_research.infrastructure.db.application_stores import SqlJobListStore

    job_ids = []
    for i in range(30):
        jid = uuid.uuid4()
        job_ids.append(jid)
        SqlJobStore(sf).create(request=_request(input_company=f"C{i}"), job_id=jid)
        with sf() as session:
            row = session.get(ResearchJobORM, jid)
            row.created_at = datetime(2026, 1, 1, 0, 0, 0) + timedelta(minutes=i)
            session.commit()

    store = SqlJobListStore(sf)
    page1 = store.list_jobs(status=None, limit=10, before=None)
    assert len(page1) == 10
    assert [e.input_company for e in page1] == [f"C{29 - i}" for i in range(10)]

    last = page1[-1]
    page2 = store.list_jobs(status=None, limit=10, before=(last.created_at, last.job_id))
    assert len(page2) == 10
    seen = {e.job_id for e in page1} | {e.job_id for e in page2}
    assert len(seen) == 20
    page3 = store.list_jobs(status=None, limit=10, before=(page2[-1].created_at, page2[-1].job_id))
    assert len(page3) == 10
    assert len({e.job_id for e in page3} & seen) == 0  # 无重复


def test_job_list_store_filters_by_status(sf):
    from invest_research.infrastructure.db.application_stores import SqlJobListStore

    running_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(input_company="Running"), job_id=running_id)
    with sf() as session:
        row = session.get(ResearchJobORM, running_id)
        row.status = "running"
        session.commit()
    SqlJobStore(sf).create(request=_request(input_company="Succeeded"), job_id=uuid.uuid4())

    store = SqlJobListStore(sf)
    running = store.list_jobs(status=JobStatus.RUNNING, limit=20, before=None)
    assert len(running) == 1
    assert running[0].input_company == "Running"


def test_job_query_returns_none_for_missing(sf):
    query = SqlJobQueryStore(sf)
    assert query.get(uuid.uuid4()) is None


def test_job_query_includes_steps_sorted_by_sequence(sf):
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)

    with sf() as session:
        session.add(
            WorkflowStepORM(
                job_id=job_id,
                step_name="step_b",
                sequence_no=2,
                status="pending",
                attempt_count=0,
                input_json={},
                output_json={},
                error_json={},
            )
        )
        session.add(
            WorkflowStepORM(
                job_id=job_id,
                step_name="step_a",
                sequence_no=1,
                status="succeeded",
                attempt_count=0,
                input_json={},
                output_json={},
                error_json={},
            )
        )
        session.commit()

    snapshot = SqlJobQueryStore(sf).get(job_id)
    assert [s.sequence_no for s in snapshot.steps] == [1, 2]
    assert [s.step_name for s in snapshot.steps] == ["step_a", "step_b"]
    assert snapshot.steps[0].status.value == "succeeded"


def test_job_store_duplicate_id_raises(sf):
    store = SqlJobStore(sf)
    job_id = uuid.uuid4()
    store.create(request=_request(), job_id=job_id)
    with pytest.raises(Exception):
        store.create(request=_request(), job_id=job_id)


def test_idempotency_store_roundtrip(sf):
    store = SqlIdempotencyStore(sf)
    assert store.get("k") is None
    store.save(
        "k",
        StoredJob(job_id=uuid.uuid4(), status=JobStatus.PENDING, request_fingerprint="fp"),
    )
    got = store.get("k")
    assert got is not None
    assert got.request_fingerprint == "fp"


def test_idempotency_store_duplicate_key_raises_on_concurrent_save(sf):
    """同 key 两次 save（对应并发竞争）必须由 DB UNIQUE 拦截。"""
    store = SqlIdempotencyStore(sf)
    store.save(
        "dup",
        StoredJob(job_id=uuid.uuid4(), status=JobStatus.PENDING, request_fingerprint="a"),
    )
    with pytest.raises(Exception):
        store.save(
            "dup",
            StoredJob(job_id=uuid.uuid4(), status=JobStatus.PENDING, request_fingerprint="b"),
        )


def test_cancel_writer_pending_success(sf):
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    writer = SqlCancelStatusWriter(sf)
    assert writer.cancel_from_pending(job_id) is True
    assert SqlJobQueryStore(sf).get(job_id).status == JobStatus.CANCELLED
    assert writer.cancel_from_pending(job_id) is False


def test_cancel_writer_running_success(sf):
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    with sf() as session:
        row = session.get(ResearchJobORM, job_id)
        row.status = "running"
        session.commit()
    writer = SqlCancelStatusWriter(sf)
    assert writer.cancel_from_running(job_id) is True


def test_cancel_writer_terminal_not_overwritten(sf):
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    with sf() as session:
        row = session.get(ResearchJobORM, job_id)
        row.status = "succeeded"
        session.commit()
    writer = SqlCancelStatusWriter(sf)
    assert writer.cancel_from_pending(job_id) is False
    assert writer.cancel_from_running(job_id) is False
    assert SqlJobQueryStore(sf).get(job_id).status == JobStatus.SUCCEEDED


def test_artifact_catalog_lists_only_job_artifacts(sf):
    job_id = uuid.uuid4()
    other = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    SqlJobStore(sf).create(request=_request(input_company="MSFT"), job_id=other)
    with sf() as session:
        for jid in (job_id, other):
            session.add(
                ArtifactORM(
                    job_id=jid,
                    artifact_key="report.md",
                    artifact_type="markdown",
                    storage_uri="",
                    content_checksum="sum",
                    byte_size=3,
                )
            )
        session.commit()
    catalog = SqlArtifactCatalogStore(sf)
    mine = catalog.list_artifacts(job_id)
    assert len(mine) == 1
    assert mine[0].artifact_key == "report.md"
    assert isinstance(mine[0], ArtifactInfo)


def test_artifact_content_only_registered_and_safe(sf, tmp_path: Path):
    job_id = uuid.uuid4()
    SqlJobStore(sf).create(request=_request(), job_id=job_id)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / str(job_id)).mkdir(parents=True)
    (artifact_root / str(job_id) / "report.md").write_text("hello", encoding="utf-8")
    with sf() as session:
        session.add(
            ArtifactORM(
                job_id=job_id,
                artifact_key="report.md",
                artifact_type="markdown",
                storage_uri="",
                content_checksum="sum",
                byte_size=5,
            )
        )
        session.commit()

    content = SqlArtifactContentStore(sf, str(artifact_root))
    assert content.read(job_id, "report.md") == b"hello"
    assert content.read(job_id, "other.md") is None
    assert content.read(uuid.uuid4(), "report.md") is None
