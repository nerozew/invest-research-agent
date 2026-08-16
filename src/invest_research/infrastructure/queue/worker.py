"""Celery worker 进程入口（P04-09：Docker Worker 启动点）。

- 模块级 ``celery_app``：通过环境变量 ``BROKER_URL`` 注入真实 Redis broker，
  供 ``docker/worker.Dockerfile`` 的 ``celery -A ... worker`` 命令引用。
- 默认内存 broker（本地/测试），保证模块导入零 Redis 连接。
- 注册 P04-07 的 Flow adapter：把 job_id 委派给 ``ExecuteResearchJobService``；
  loader/writer 使用真实 ``JobRepository``（惰性构建 DB 连接），
  flow_runner 用 ``ResearchFlowRunner``（fake 逻辑 Flow，P03 已验证 00-07 全链）。
- P06-06B：Worker 开始处理 Job 时幂等创建 00-07 步骤并标记实时进度；
  Job 失败时收口 running 步骤，终态清空 current_step。

模块导入零 DB/Redis 连接：engine/session 在 ``process`` 首次调用时
才由 ``_session_factory()`` 惰性创建。
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from invest_research.application.execution import ExecuteResearchJobService
from invest_research.application.progress import ProgressSink, StepRecordError
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus
from invest_research.infrastructure.db.models import ResearchJob as ResearchJobORM
from invest_research.infrastructure.db.progress import SqlProgressSink
from invest_research.infrastructure.db.repositories import JobRepository
from invest_research.infrastructure.performance import PerformanceRecorder
from invest_research.infrastructure.queue.celery_app import create_celery_app
from invest_research.infrastructure.queue.execution_recorder import ExecutionRecorder
from invest_research.infrastructure.queue.flow_adapter import (
    ResearchFlowRunner,
    ResearchJobExecutionHandler,
)
from invest_research.infrastructure.queue.tasks import register_tasks
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache

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


def _build_live_research_tools(
    settings: Any, stats: dict[str, int] | None = None
) -> list[Any]:
    """构造 live 模式的 Research 真实工具白名单（SEC/搜索/下载）。

    - 兼容旧名称；内部委托 live_resources.build_live_client_and_serper 完成
      Serper Key 校验与共享 client/serper 构建（该函数不依赖 Celery/CrewAI）；
    - ``stats``：可选调用统计 dict（P05-13 验收：外部调用证据写入 manifest）；
    - 返回 from real_tools.build_research_tools 的 CrewAI 工具列表。
    """
    from invest_research.infrastructure.live_resources import build_live_client_and_serper
    from invest_research.infrastructure.real_tools import (
        build_research_toolkit,
        build_research_tools,
    )

    client, serper = build_live_client_and_serper(settings)
    toolkit = build_research_toolkit(client=client, serper=serper)
    return build_research_tools(toolkit=toolkit, stats=stats)


@dataclass
class LiveResearchComponents:
    """live 模式组装的完整研究组件（工具 + 缓存 + 计时 + 预算 + 并行预取）。"""

    research_tools: list[Any]
    cache: ToolCallCache
    recorder: PerformanceRecorder
    budget: ToolBudget
    prefetch: Callable[[ResearchRequest], Any]


def _build_live_components(
    settings: Any, stats: dict[str, int] | None = None
) -> LiveResearchComponents:
    """构建 live 模式的工具、缓存、性能记录器与并行预取（P05.5）。"""
    from invest_research.infrastructure.live_resources import build_live_client_and_serper
    from invest_research.infrastructure.real_tools import (
        build_research_prefetcher,
        build_research_toolkit,
        build_research_tools,
    )

    client, serper = build_live_client_and_serper(settings)
    toolkit = build_research_toolkit(client=client, serper=serper)
    cache = ToolCallCache()
    recorder = PerformanceRecorder()
    budget = ToolBudget()
    research_tools = build_research_tools(
        toolkit=toolkit, stats=stats, recorder=recorder, cache=cache, budget=budget
    )
    prefetch = build_research_prefetcher(
        toolkit=toolkit, cache=cache, recorder=recorder, budget=budget, stats=stats
    )
    return LiveResearchComponents(
        research_tools=research_tools,
        cache=cache,
        recorder=recorder,
        budget=budget,
        prefetch=prefetch,
    )


def _default_flow_runner() -> ResearchFlowRunner:
    """按 FLOW_MODE 环境变量构建 FlowRunner（默认 fake，不读 Settings/不依赖 key）。

    - ``FLOW_MODE=fake``（默认）：返回 ``ResearchFlowRunner``（P03 纯 fake 00-07 全链，
      不联网、不产生模型费用），保持 worker 模块导入零 Settings 依赖；
    - ``FLOW_MODE=live``：委托 ``flow_wiring.build_flow_runner(get_settings())``，
      LLM API Key / Serper Key 缺失或为空时 fail-fast（可读错误），
      不允许缺配置启动真实模型运行。
    """
    if os.environ.get("FLOW_MODE", "fake") == "fake":
        return ResearchFlowRunner()
    from invest_research.infrastructure.flow_wiring import build_flow_runner
    from invest_research.settings import get_settings

    settings = get_settings()
    stats: dict[str, int] = {}
    components = _build_live_components(settings, stats=stats)
    runner = build_flow_runner(
        settings,
        research_tools=components.research_tools,
        stats=stats,
        recorder=components.recorder,
        cache=components.cache,
        prefetch=components.prefetch,
        budget=components.budget,
    )
    return runner  # type: ignore[return-value]


def _progress_logger() -> logging.Logger:
    return logging.getLogger(__name__)


def _build_handler(flow_runner: ResearchFlowRunner | None = None) -> ResearchJobExecutionHandler:
    """构造 worker 侧 handler：真实 Repository 加载/写状态 + Flow 执行。

    - 未显式传入 ``flow_runner`` 时按 ``FLOW_MODE`` 环境变量构建（默认 fake）；
    - loader：``JobRepository.get(job)`` → 用 ORM 字段重建 ``ResearchRequest``；
    - writer：``JobRepository.update_status``（pending→running，终态 succeeded/failed）；
    - P06-06B：SqlProgressSink 注入 loader（flow 运行前设置 job_id/progress），
      writer 在 running/终态维护 current_step 与步骤收口。
    """
    session_factory = _build_session_factory()
    repo = JobRepository(session_factory)
    # P06-06B：实时步骤进度端口（SQL 实现，短事务；写入失败不影响任务）
    progress: ProgressSink = SqlProgressSink(session_factory)

    class _RepoLoader:
        def load(self, job_id: uuid.UUID) -> ResearchRequest | None:
            job = repo.get(job_id)
            if job is None:
                return None
            # P06-06B：把 job_id/progress 注入 flow runner（run 前由 ExecutionService
            #  先调用 loader，再调用 flow_runner.run(request)——顺序保证注入生效）。
            runner.progress = progress
            runner.job_id = job_id
            # P06-06B 收口：请求加载成功后立即把 00_request 标记 succeeded，
            # 再开始 01_company_resolve（保证"00 在 01 前 succeeded"的不变量；
            # 重复标记对已成功步骤是安全无操作）。
            try:
                progress.mark_step_succeeded(job_id, "00_request")
            except StepRecordError as exc:
                _progress_logger().warning("标记 00_request 成功失败 job=%s: %s", job_id, exc)
            return ResearchRequest(
                input_company=job.input_company,
                as_of_date=job.as_of_date,
                language=job.language,
                requested_forms=tuple(job.requested_forms),
                # P06-06A：把每个 Job 持久化的研究档位传给 FlowRunner
                research_profile=job.research_profile,
            )

    class _RepoWriter:
        def mark_running(self, job_id: uuid.UUID) -> bool:
            # 条件更新 pending→running 并记录 started_at（乐观锁防重复执行）
            with session_factory() as session:
                row = session.get(ResearchJobORM, job_id)
                if row is None or row.status != JobStatus.PENDING.value:
                    return False
                row.status = JobStatus.RUNNING.value
                row.started_at = datetime.now(timezone.utc)
                session.commit()
            # P06-06B：幂等创建 00-07 步骤并标记 00_request running（重复投递不重复插入）
            try:
                progress.initialize_steps(job_id)
                progress.mark_step_running(job_id, "00_request")
            except StepRecordError as exc:
                _progress_logger().warning("进度初始化失败 job=%s: %s", job_id, exc)
            return True

        def mark_succeeded(self, job_id: uuid.UUID) -> None:
            with session_factory() as session:
                row = session.get(ResearchJobORM, job_id)
                if row is None:
                    return
                row.status = JobStatus.SUCCEEDED.value
                row.completed_at = datetime.now(timezone.utc)
                row.current_step = None  # P06-06B：终态清空 current_step
                session.commit()

        def mark_failed(self, job_id: uuid.UUID) -> None:
            # P05.5-deploy-fix：Flow 异常 → running → failed（防止任务永久卡 running）
            with session_factory() as session:
                row = session.get(ResearchJobORM, job_id)
                if row is None:
                    return
                row.status = JobStatus.FAILED.value
                row.completed_at = datetime.now(timezone.utc)
                row.current_step = None  # P06-06B：终态清空 current_step
                session.commit()
            # P06-06B：收口所有仍为 running 的步骤为 failed_terminal（不留虚假 running）
            try:
                progress.fail_all_running_steps(
                    job_id,
                    error_code="FLOW_EXECUTION_FAILED",
                    error_message="任务执行失败，流程异常终止",
                )
            except StepRecordError as exc:
                _progress_logger().warning("收口 running 步骤失败 job=%s: %s", job_id, exc)

    runner = flow_runner if flow_runner is not None else _default_flow_runner()
    service = ExecuteResearchJobService(
        loader=_RepoLoader(),
        writer=_RepoWriter(),
        flow_runner=runner,
    )

    # P05.5-opt：成功执行后把步骤/工件/耗时落库（失败只告警，不回滚已成功任务）
    recorder = ExecutionRecorder(
        session_factory, os.environ.get("ARTIFACT_ROOT", "artifacts")
    )

    def _record_execution(job_id: uuid.UUID) -> None:
        try:
            recorder.record(job_id, getattr(runner, "last_state", None))
        except Exception as exc:  # noqa: BLE001 - 记录失败不影响任务结果
            _progress_logger().warning("执行记录落库失败 job=%s: %s", job_id, exc)

    return ResearchJobExecutionHandler(service, recorder=_record_execution)


def _setup_otel_from_env() -> None:
    """worker 进程启动时初始化 OpenTelemetry（P06-05）。

    直接读环境变量（不依赖 Settings 导入），保持 worker 模块"零 Settings 依赖"：
    - OTEL_EXPORTER_OTLP_ENDPOINT 配置时走 OTLP 批量导出（配合本地 collector）；
    - 缺省控制台导出（本地直接看 stdout）。
    """
    from invest_research.infrastructure.observability.tracing import setup_tracing

    setup_tracing(
        service_name=os.environ.get("OTEL_SERVICE_NAME", "invest-research"),
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )


def _build_celery_app() -> Celery:
    """构建 Celery app：broker 取环境变量 BROKER_URL，缺省 memory://。"""
    broker = os.environ.get("BROKER_URL", "memory://")
    _setup_otel_from_env()
    app = create_celery_app(broker_url=broker)
    register_tasks(app, _build_handler())
    return app


celery_app: Celery = _build_celery_app()
