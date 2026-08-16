"""生产 composition root（P04-10A + P05-03B）。

把基础设施真实实现（SQLAlchemy engine/session、Store Adapter、
Celery dispatcher、Transactional Outbox）组装进 ``api.create_app``，
产出完整可用的生产 FastAPI 应用。

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
from invest_research.application.outbox import OutboxRelayCounter, OutboxRelayService
from invest_research.infrastructure.db.application_stores import (
    SqlArtifactCatalogStore,
    SqlArtifactContentStore,
    SqlCancelStatusWriter,
    SqlIdempotencyStore,
    SqlJobListStore,
    SqlJobQueryStore,
    SqlJobStore,
    SqlOutboxStore,
)
from invest_research.infrastructure.db.base import (
    create_db_engine,
    create_session_factory,
)
from invest_research.infrastructure.db.progress import SqlProgressSink
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
        job_list_store: SqlJobListStore,
        idempotency_store: SqlIdempotencyStore,
        cancel_status_writer: SqlCancelStatusWriter,
        progress_sink: SqlProgressSink,
        artifact_catalog_store: SqlArtifactCatalogStore,
        artifact_content_store: SqlArtifactContentStore,
        dispatcher: CeleryJobDispatcher,
        outbox_store: SqlOutboxStore,
        outbox_relay: OutboxRelayService,
        outbox_counter: OutboxRelayCounter,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.session_factory = session_factory
        self.checker = checker
        self.job_store = job_store
        self.job_query_store = job_query_store
        self.job_list_store = job_list_store
        self.idempotency_store = idempotency_store
        self.cancel_status_writer = cancel_status_writer
        self.progress_sink = progress_sink
        self.artifact_catalog_store = artifact_catalog_store
        self.artifact_content_store = artifact_content_store
        self.dispatcher = dispatcher
        self.outbox_store = outbox_store
        self.outbox_relay = outbox_relay
        self.outbox_counter = outbox_counter


def create_production_app(
    settings: Settings | None = None,
) -> tuple[FastAPI, ProductionContainer]:
    """创建生产 FastAPI 应用与依赖容器（P04-10A 入口）。"""
    resolved = settings or get_settings()

    # P06-05：进程启动时初始化 OpenTelemetry（OTLP 端点或控制台导出）。
    from invest_research.infrastructure.observability.tracing import setup_tracing

    setup_tracing(
        service_name=resolved.otel_service_name,
        endpoint=resolved.otel_exporter_otlp_endpoint,
        batch_interval_ms=resolved.otel_batch_export_interval_ms,
    )

    engine = create_db_engine(resolved.database_url)
    session_factory = create_session_factory(engine)
    checker = build_health_checker(resolved)

    job_store = SqlJobStore(session_factory)
    job_query_store = SqlJobQueryStore(session_factory)
    job_list_store = SqlJobListStore(session_factory)
    idempotency_store = SqlIdempotencyStore(session_factory)
    cancel_status_writer = SqlCancelStatusWriter(session_factory)
    # P06-06B 收口：取消服务收口步骤（running→skipped、pending→skipped、清空 current_step）
    progress_sink = SqlProgressSink(session_factory)
    artifact_catalog_store = SqlArtifactCatalogStore(session_factory)
    artifact_content_store = SqlArtifactContentStore(session_factory, resolved.artifact_root)

    celery_app = create_celery_app(broker_url=resolved.broker_url)
    dispatcher = CeleryJobDispatcher(celery_app)

    # P05-03B：Transactional Outbox —— 事件与 Job 同事务写入，relay 恢复未投递。
    outbox_store = SqlOutboxStore(session_factory)
    outbox_relay = OutboxRelayService(store=outbox_store, dispatcher=dispatcher)
    outbox_counter = OutboxRelayCounter()

    container = ProductionContainer(
        settings=resolved,
        engine=engine,
        session_factory=session_factory,
        checker=checker,
        job_store=job_store,
        job_query_store=job_query_store,
        job_list_store=job_list_store,
        idempotency_store=idempotency_store,
        cancel_status_writer=cancel_status_writer,
        progress_sink=progress_sink,
        artifact_catalog_store=artifact_catalog_store,
        artifact_content_store=artifact_content_store,
        dispatcher=dispatcher,
        outbox_store=outbox_store,
        outbox_relay=outbox_relay,
        outbox_counter=outbox_counter,
    )

    app = create_app(
        settings=resolved,
        health_checker=checker,
        job_store=job_store,
        job_query_store=job_query_store,
        job_list_store=job_list_store,
        idempotency_store=idempotency_store,
        cancel_status_writer=cancel_status_writer,
        cancel_step_cleanup=progress_sink,
        artifact_catalog_store=artifact_catalog_store,
        artifact_content_store=artifact_content_store,
        job_dispatcher=dispatcher,
        outbox_relay_service=outbox_relay,
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
