"""P01-12 JobRepository 集成测试：create / get / status 条件更新。"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from invest_research.domain.status import JobStatus
from invest_research.infrastructure.db.base import create_session_factory
from invest_research.infrastructure.db.models import ResearchJob
from invest_research.infrastructure.db.repositories import JobRepository

RepoEnv = tuple[JobRepository, sessionmaker[Session], Engine]


def _docker_available() -> bool:
    try:
        from docker import from_env  # type: ignore[import-untyped]

        client = from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available() or os.environ.get("SKIP_DB_TESTS") == "1",
    reason="需要运行时 Docker（testcontainers）",
)


def _norm(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


@pytest.fixture()
def env(pg_container_url: str) -> RepoEnv:
    """建表后返回 (repository, session 工厂, engine)。"""
    engine = create_engine(_norm(pg_container_url))
    session_factory = create_session_factory(engine)
    repo = JobRepository(session_factory)
    return repo, session_factory, engine


@pytest.fixture(scope="module")
def pg_container() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="module")
def pg_container_url(pg_container: str) -> str:
    """运行全部迁移到 head，返回原始 URL。"""
    url = _norm(pg_container)
    root = Path(os.path.abspath(__file__)).parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return pg_container


def _make_job() -> ResearchJob:
    return ResearchJob(
        input_company="Test Corp",
        as_of_date=date(2026, 1, 1),
        language="zh-CN",
        requested_forms=["10-K"],
        status="pending",
        config_snapshot={},
    )


def test_create_then_get(env: RepoEnv) -> None:
    """create 后 get 能取回同一条任务。"""
    repo, _sf, _eng = env
    job = _make_job()
    created = repo.create(job)
    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.input_company == "Test Corp"
    assert fetched.status == JobStatus.PENDING.value


def test_get_missing_returns_none(env: RepoEnv) -> None:
    """不存在的 id 返回 None。"""
    repo, _sf, _eng = env
    assert repo.get(uuid.uuid4()) is None


def test_update_status_success_when_matching(env: RepoEnv) -> None:
    """当前状态匹配时条件更新成功。"""
    repo, _sf, _eng = env
    job = _make_job()
    created = repo.create(job)

    ok = repo.update_status(created.id, JobStatus.PENDING, JobStatus.RUNNING)
    assert ok is True
    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.status == JobStatus.RUNNING.value


def test_update_status_rejected_when_stale(env: RepoEnv) -> None:
    """当前状态不匹配（过期条件）时更新被拒绝，且状态不被覆盖。"""
    repo, _sf, _eng = env
    job = _make_job()
    created = repo.create(job)

    # 先把任务推进到 running
    assert repo.update_status(created.id, JobStatus.PENDING, JobStatus.RUNNING)

    # 再用过期的 from_status=pending 试图覆盖 → 应失败且状态保持 running
    ok = repo.update_status(created.id, JobStatus.PENDING, JobStatus.FAILED)
    assert ok is False
    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.status == JobStatus.RUNNING.value
