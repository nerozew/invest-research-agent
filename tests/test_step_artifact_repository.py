"""P01-13 Step/Artifact Repository 集成测试：原子成功提交 + 重复写拒绝。"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import db_integration_enabled
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from invest_research.domain.status import StepStatus
from invest_research.infrastructure.db.base import create_session_factory
from invest_research.infrastructure.db.models import Artifact, ResearchJob, WorkflowStep
from invest_research.infrastructure.db.repositories import (
    ArtifactRepository,
    JobRepository,
    StepRepository,
)

RepoEnv = tuple[JobRepository, StepRepository, ArtifactRepository, sessionmaker[Session]]

pytestmark = pytest.mark.skipif(
    not db_integration_enabled(),
    reason="需要运行时 Docker（testcontainers）或 TEST_DATABASE_URL",
)


def _norm(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


@pytest.fixture()
def env(pg_container_url: str) -> RepoEnv:
    """返回 (job_repo, step_repo, artifact_repo, session_factory)。"""
    engine = create_engine(_norm(pg_container_url))
    session_factory = create_session_factory(engine)
    return (
        JobRepository(session_factory),
        StepRepository(session_factory),
        ArtifactRepository(session_factory),
        session_factory,
    )


@pytest.fixture(scope="module")
def pg_container_url(pg_container: str) -> str:
    url = _norm(pg_container)
    root = Path(os.path.abspath(__file__)).parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return pg_container


def _mk_job() -> ResearchJob:
    return ResearchJob(
        input_company="Test Corp",
        as_of_date=date(2026, 1, 1),
        language="zh-CN",
        requested_forms=["10-K"],
        status="pending",
        config_snapshot={},
    )


def test_step_create_and_conditional_update(env: RepoEnv) -> None:
    """步骤可创建；条件更新（乐观锁）成功与过期条件被拒均正确。"""
    job_repo, step_repo, _art_repo, _sf = env
    job = job_repo.create(_mk_job())
    step = WorkflowStep(
        job_id=job.id,
        step_name="01_company_resolve",
        sequence_no=1,
        status="pending",
        input_json={},
        output_json={},
        error_json={},
    )
    created = step_repo.create(step)
    assert created.id is not None

    # 条件匹配 → 成功
    assert step_repo.update_status(created.id, StepStatus.PENDING, StepStatus.RUNNING) is True
    # 过期条件 → 拒绝
    assert (
        step_repo.update_status(created.id, StepStatus.PENDING, StepStatus.FAILED_TERMINAL) is False
    )

    steps = step_repo.list_by_job(job.id)
    assert len(steps) == 1
    assert steps[0].status == StepStatus.RUNNING.value


def test_artifact_duplicate_write_rejected(env: RepoEnv) -> None:
    """同 job_id+artifact_key 二次写工件被数据库唯一约束拒绝。"""
    job_repo, _step_repo, art_repo, _sf = env
    job = job_repo.create(_mk_job())

    a1 = Artifact(
        job_id=job.id,
        artifact_key="k1",
        artifact_type="json",
        storage_uri="u1",
        content_checksum="c1",
        byte_size=10,
    )
    created = art_repo.create(a1)
    assert created.id is not None

    a2 = Artifact(
        job_id=job.id,
        artifact_key="k1",
        artifact_type="json",
        storage_uri="u2",
        content_checksum="c2",
        byte_size=20,
    )
    from sqlalchemy import exc as sqlexc

    with pytest.raises(sqlexc.IntegrityError):
        art_repo.create(a2)


def test_atomic_job_and_steps_single_commit(env: RepoEnv) -> None:
    """job + 第一个 step 在同一事务提交（原子），提交后都能查到。"""
    _job_repo, step_repo, _art_repo, sf = env
    with sf() as session:
        job = ResearchJob(
            input_company="Atomic",
            as_of_date=date(2026, 2, 1),
            language="zh-CN",
            requested_forms=["10-K"],
            status="pending",
            config_snapshot={},
        )
        session.add(job)
        session.flush()  # 立即分配 job.id（仍在同一事务，原子性保持）
        session.add(
            WorkflowStep(
                job_id=job.id,
                step_name="00_validate",
                sequence_no=0,
                status="pending",
                input_json={},
                output_json={},
                error_json={},
            )
        )
        session.commit()
        job_id = job.id

    # 提交后两个都持久化可见
    steps = step_repo.list_by_job(job_id)
    assert len(steps) == 1
    assert steps[0].step_name == "00_validate"
