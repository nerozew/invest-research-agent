"""Celery worker 进程入口（P04-09：Docker Worker 启动点）。

- 模块级 ``celery_app``：通过环境变量 ``BROKER_URL`` 注入真实 Redis broker，
  供 ``docker/worker.Dockerfile`` 的 ``celery -A ... worker`` 命令引用。
- 默认内存 broker（本地/测试），保证模块导入零 Redis 连接。
- 注册 P04-07 的 Flow adapter：把 job_id 委派给 ``ExecuteResearchJobService``；
  loader/writer 使用真实 ``JobRepository``（惰性构建 DB 连接），
  flow_runner 用 ``ResearchFlowRunner``（fake 逻辑 Flow，P03 已验证 00-07 全链）。

模块导入零 DB/Redis 连接：engine/session 在 ``process`` 首次调用时
才由 ``_session_factory()`` 惰性创建。
"""

from __future__ import annotations

import os
import uuid
from typing import Callable

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from invest_research.application.execution import ExecuteResearchJobService
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.db.repositories import JobRepository
from invest_research.infrastructure.queue.celery_app import create_celery_app
from invest_research.infrastructure.queue.flow_adapter import (
    ResearchFlowRunner,
    ResearchJobExecutionHandler,
)
from invest_research.infrastructure.queue.tasks import register_tasks

__all__ = ["celery_app"]

SessionFactory = Callable[[], Session]


def _build_session_factory() -> SessionFactory:
    """惰性构建 DB session 工厂（首次调用时才创建 engine 连接池）。"""
    from sqlalchemy import create_engine

    database_url = os.environ.get("DATABASE_URL", "sqlite+pysqlite:///:memory:")

    def _make_engine() -> Engine:
        return create_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
        )

    engine: Engine | None = None

    def factory() -> Session:
        nonlocal engine
        if engine is None:
            engine = _make_engine()
        return Session(bind=engine, autoflush=False)

    return factory


def _build_handler() -> ResearchJobExecutionHandler:
    """构造 worker 侧 handler：真实 Repository 加载/写状态 + fake Flow 执行。

    - loader：``JobRepository.get(job)`` → 用 ORM 字段重建 ``ResearchRequest``；
    - writer：``JobRepository.update_status``（pending→running、running→succeeded）；
    - flow_runner：``ResearchFlowRunner``（P03 fake 00-07 全链，不联网）。
    """
    repo = JobRepository(_build_session_factory())

    class _RepoLoader:
        def load(self, job_id: uuid.UUID) -> ResearchRequest | None:
            job = repo.get(job_id)
            if job is None:
                return None
            return ResearchRequest(
                input_company=job.input_company,
                as_of_date=job.as_of_date,
                language=job.language,
                requested_forms=tuple(job.requested_forms),
            )

    class _RepoWriter:
        def mark_running(self, job_id: uuid.UUID) -> bool:
            from invest_research.domain.status import JobStatus

            return repo.update_status(job_id, JobStatus.PENDING, JobStatus.RUNNING)

        def mark_succeeded(self, job_id: uuid.UUID) -> None:
            from invest_research.domain.status import JobStatus

            repo.update_status(job_id, JobStatus.RUNNING, JobStatus.SUCCEEDED)

    service = ExecuteResearchJobService(
        loader=_RepoLoader(),
        writer=_RepoWriter(),
        flow_runner=ResearchFlowRunner(),
    )
    return ResearchJobExecutionHandler(service)


def _build_celery_app() -> Celery:
    """构建 Celery app：broker 取环境变量 BROKER_URL，缺省 memory://。"""
    broker = os.environ.get("BROKER_URL", "memory://")
    app = create_celery_app(broker_url=broker)
    register_tasks(app, _build_handler())
    return app


celery_app: Celery = _build_celery_app()
