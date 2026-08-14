"""生产 composition root（P04-10A）。

把基础设施真实实现（SQLAlchemy engine/session、6 个 Store Adapter、
Celery dispatcher）组装进 ``api.create_app``，产出完整可用的生产 FastAPI 应用。

职责与约束：
- 读 Settings（``DATABASE_URL``/``REDIS_URL``/``BROKER_URL`` 来自环境变量）；
- 显式 build 依赖并注入，不在 ``api.app`` 的模块导入时连接数据库/Redis；
- 提供 ``dispose_production_resources`` 供 lifespan 释放 engine/Redis；
- 本模块是"组装层"，不包含业务逻辑。
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from invest_research.api.app import create_app
from invest_research.api.health import (
    DependencyHealthChecker,
    build_health_checker,
    dispose_dependency_resources,
)
from invest_research.infrastructure.db.application_stores import (
    SqlArtifactCatalogStore,
    SqlArtifactContentStore,
    SqlCancelStatusWriter,
    SqlIdempotencyStore,
    SqlJobQueryStore,
    SqlJobStore,
)
from invest_research.infrastructure.db.base import (
    create_db_engine,
    create_session_factory,
)
from invest_research.infrastructure.db.repositories import SessionFactory
from invest_research.infrastructure.queue.celery_app import create_celery_app
from invest_research.infrastructure.queue.job_dispatcher import CeleryJobDispatcher
from invest_research.settings import Settings, get_settings

__all__ = [
    "ProductionContainer",
    "create_production_app",
    "create_production_app_factory",
    "dispose_production_resources",
]


class ProductionContainer:
    """生产依赖容器：持有 engine/session 工厂/各 store，便于 lifespan 释放。"""

    def __init__(
        self,
        *,
        settings: Settings,
        engine: Engine,
        session_factory: SessionFactory,
        checker: DependencyHealthChecker,
        job_store: SqlJobStore,
        job_query_store: SqlJobQueryStore,
        idempotency_store: SqlIdempotencyStore,
        cancel_status_writer: SqlCancelStatusWriter,
        artifact_catalog_store: SqlArtifactCatalogStore,
        artifact_content_store: SqlArtifactContentStore,
        dispatcher: CeleryJobDispatcher,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.session_factory = session_factory
        self.checker = checker
        self.job_store = job_store
        self.job_query_store = job_query_store
        self.idempotency_store = idempotency_store
        self.cancel_status_writer = cancel_status_writer
        self.artifact_catalog_store = artifact_catalog_store
        self.artifact_content_store = artifact_content_store
        self.dispatcher = dispatcher


def create_production_app(
    settings: Settings | None = None,
) -> tuple[FastAPI, ProductionContainer]:
    """创建生产 FastAPI 应用与依赖容器（P04-10A 入口）。

    返回 (app, container)。调用方（uvicorn 使用 ``--factory`` 需要
    模块级 ``create_production_app`` 可被 uvicorn 当作 factory 直接调用，
    因此这里额外提供包装函数 ``create_production_app_factory``。
    """
    resolved = settings or get_settings()

    engine = create_db_engine(resolved.database_url)
    session_factory = create_session_factory(engine)
    checker = build_health_checker(resolved)

    job_store = SqlJobStore(session_factory)
    job_query_store = SqlJobQueryStore(session_factory)
    idempotency_store = SqlIdempotencyStore(session_factory)
    cancel_status_writer = SqlCancelStatusWriter(session_factory)
    artifact_catalog_store = SqlArtifactCatalogStore(session_factory)
    artifact_content_store = SqlArtifactContentStore(
        session_factory, resolved.artifact_root
    )

    celery_app = create_celery_app(broker_url=resolved.broker_url)
    dispatcher = CeleryJobDispatcher(celery_app)

    container = ProductionContainer(
        settings=resolved,
        engine=engine,
        session_factory=session_factory,
        checker=checker,
        job_store=job_store,
        job_query_store=job_query_store,
        idempotency_store=idempotency_store,
        cancel_status_writer=cancel_status_writer,
        artifact_catalog_store=artifact_catalog_store,
        artifact_content_store=artifact_content_store,
        dispatcher=dispatcher,
    )

    app = create_app(
        settings=resolved,
        health_checker=checker,
        job_store=job_store,
        job_query_store=job_query_store,
        idempotency_store=idempotency_store,
        cancel_status_writer=cancel_status_writer,
        artifact_catalog_store=artifact_catalog_store,
        artifact_content_store=artifact_content_store,
        job_dispatcher=dispatcher,
    )
    # 把 container 挂到 app.state 供 lifespan 释放
    app.state.production_container = container
    return app, container


def create_production_app_factory() -> FastAPI:
    """uvicorn 可用 factory：返回 app（container 由 app.state 持有）。"""
    app, _ = create_production_app()
    return app


def dispose_production_resources(
    container: ProductionContainer,
) -> None:
    """释放 production 持有的外部资源（engine/redis）。"""
    try:
        dispose_dependency_resources(container.checker)
    finally:
        container.engine.dispose()
