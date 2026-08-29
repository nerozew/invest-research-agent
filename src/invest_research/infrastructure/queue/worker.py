"""Celery worker 进程入口（P04-09：Docker Worker 启动点）。

- 模块级 ``celery_app``：通过环境变量 ``BROKER_URL`` 注入真实 Redis broker，
  供 ``docker/worker.Dockerfile`` 的 ``celery -A ... worker`` 命令引用。
- 默认内存 broker（本地/测试），保证模块导入零 Redis 连接。
- 注册 P04-07 的 Flow adapter：把 job_id 委派给 ``ExecuteResearchJobService``；
  loader/writer 使用真实 ``JobRepository``（惰性构建 DB 连接），
  flow_runner 用 ``ResearchFlowRunner``（fake 逻辑 Flow，P03 已验证 00-07 全链）。
- P06-06B：Worker 开始处理 Job 时幂等创建 00-07 步骤并标记实时进度；
  Job 失败时收口 running 步骤，终态清空 current_step。
- P06-09：Worker 启动时收口历史 stale running Job（不删除记录/工件），
  并让 mark_failed 保存稳定 error_code/error_message/failure_stage。

模块导入零 DB/Redis 连接：engine/session 在 ``process`` 首次调用时
才由 ``_session_factory()`` 惰性创建。
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# P06-09C-fix：必须在任何可能触发 prometheus_client/metrics 导入的代码之前设置，
# 否则 metrics.py 模块导入时 PROMETHEUS_MULTIPROC_DIR 为 None → prometheus_client
# 走单进程模式，Celery 子进程不写 .db 文件，父进程 9101 聚合端点拿不到业务指标。
os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_metrics")

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from invest_research.application.execution import ExecuteResearchJobService
from invest_research.application.progress import ProgressSink, StepRecordError
from invest_research.domain.annual_pipeline import ResearchMode
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus
from invest_research.infrastructure.db.models import ResearchJob as ResearchJobORM
from invest_research.infrastructure.db.progress import SqlProgressSink
from invest_research.infrastructure.db.repositories import JobRepository
from invest_research.infrastructure.flow_wiring import JobResearchComponents
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
from invest_research.reporting.artifact_publisher import ReportArtifactPublisher

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


def _build_annual_web_search_pipeline(
    settings: Any, client: Any, artifact_root: Path
) -> Any | None:
    """Serper Key 存在时构造年度网页搜索管道；缺失或失败时返回 None（搜索增强降级关闭）。

    网页搜索是年度叙事章节的可选增强：Key 缺失/构造失败不阻塞核心 SEC 证据流程。
    复用 Worker 已创建的共享 httpx.Client（由调用方在 finally 中统一关闭）。
    """
    if getattr(settings, "serper_api_key", None) is None:
        return None
    from invest_research.infrastructure.annual_web_search_pipeline import (
        WebSearchEvidencePipeline,
    )
    from invest_research.tools.google_search import GoogleSearchTool
    from invest_research.tools.serper_adapter import SerperAdapter, SerperConfig

    try:
        serper = SerperAdapter(
            client,
            SerperConfig(
                api_key=settings.serper_api_key,
                endpoint=getattr(settings, "serper_endpoint", None),
            ),
        )
        return WebSearchEvidencePipeline(artifact_root, GoogleSearchTool(provider=serper))
    except Exception:  # noqa: BLE001 - 搜索增强尽力而为，失败不阻塞年度核心流程
        return None


def _build_live_research_tools(settings: Any, stats: dict[str, int] | None = None) -> list[Any]:
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


def _build_diagnostics_capture(settings: Any, job_id: str | None = None) -> Any | None:
    """P06-11K-5：按 Settings 构建 Job-local DiagnosticCapture（off 时返回 None）。

    - Settings 的 DIAGNOSTIC_CAPTURE_MODE=off（默认）返回 None，生产路径
      tools/flow runner 全部走 no-op，不改变业务成功/失败路径；
    - 非 off 时创建全新 ``BoundedDiagnosticBuffer`` + ``DiagnosticCapture``，
      闭包不捕获跨 Job 的对象；buffer.job_id 由调用方传入（每次 run 刷新）。
    """
    from invest_research.application.diagnostics.capture import DiagnosticCapture
    from invest_research.application.diagnostics.models import (
        DiagnosticCaptureMode,
        DiagnosticCapturePolicy,
    )
    from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer

    if settings.diagnostic_capture_mode == "off":
        return None
    policy = DiagnosticCapturePolicy(
        capture_mode=DiagnosticCaptureMode(settings.diagnostic_capture_mode),
        max_event_bytes=settings.diagnostic_max_event_bytes,
        max_bundle_bytes=settings.diagnostic_max_bundle_bytes,
        max_events=settings.diagnostic_max_events,
        retention_days=settings.diagnostic_retention_days,
    )
    return DiagnosticCapture(
        buffer=BoundedDiagnosticBuffer(job_id=job_id or "job-local", policy=policy)
    )


@dataclass
class LiveComponentFactory:
    """P06-11K-5：live 模式每 Job 组件工厂 + Job-local 诊断工厂。

    - ``build_components``：每次调用新建整套 job-local 组件
      （budget/cache/recorder/stats/research_tools/prefetch），工具闭包经
      ``diagnostics_provider`` 惰性读取当前 Job 的 DiagnosticCapture；
    - ``diagnostics_factory``：FlowRunner 每次 run() 调用产出新的 Job-local
      DiagnosticCapture（off 时返回 None），与 tools provider 共享同一 state；
    - ``set_job_id``：每次 run 前由 ``_PerJobFlowRunner`` 刷新当前 job_id，
      使 capture 的 buffer.job_id 与任务一致。
    """

    build_components: Callable[[], JobResearchComponents]
    diagnostics_factory: Callable[[], Any | None]
    set_job_id: Callable[[uuid.UUID | None], None]


def _build_live_components(
    settings: Any,
    stats: dict[str, int] | None = None,
    diagnostics_provider: Callable[[], Any | None] | None = None,
) -> LiveResearchComponents:
    """构建 live 模式的工具、缓存、性能记录器与并行预取（P05.5）。

    ``diagnostics_provider``：P06-11K-5 可选惰性读取当前 Job 的
    DiagnosticCapture（None 时不捕获，行为与之前完全一致）。
    """
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
        toolkit=toolkit,
        stats=stats,
        recorder=recorder,
        cache=cache,
        budget=budget,
        diagnostics_provider=diagnostics_provider,
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


def _build_live_component_factory(settings: Any) -> LiveComponentFactory:
    """P06-11G：返回每 Job 组件工厂 + Job-local 诊断工厂组合。

    - ``build_components`` 每次调用创建全新的 ToolBudget / ToolCallCache /
      PerformanceRecorder / stats / research_tools / prefetch，工具闭包只捕获
      当前 Job 的对象；``diagnostics_provider`` 惰性读取当前 Job 的 capture；
    - ``diagnostics_factory`` 由 FlowRunner 每次 run() 调用产出新的 Job-local
      DiagnosticCapture（P06-11K-5 生产接线：capture 与 tools provider 共享
      同一 state，保证工具摘要/LLM 摘要写入当前 Job 的 buffer）。
    """
    from invest_research.infrastructure.real_tools import (
        build_research_prefetcher,
        build_research_toolkit,
        build_research_tools,
    )

    # P06-11K-5：共享 Job-local 诊断状态（capture 由 diagnostics_factory 在
    # 每次 run 创建；tools provider 惰性读取，保证工具闭包不绑定旧 capture）。
    state: dict[str, Any] = {"capture": None, "job_id": None}

    def set_job_id(job_id: uuid.UUID | None) -> None:
        state["job_id"] = str(job_id) if job_id is not None else None

    def diagnostics_factory() -> Any | None:
        capture = _build_diagnostics_capture(settings, job_id=state.get("job_id"))
        state["capture"] = capture
        return capture

    def diagnostics_provider() -> Any | None:
        return state.get("capture")

    def factory() -> JobResearchComponents:
        from invest_research.infrastructure.live_resources import build_live_client_and_serper

        client, serper = build_live_client_and_serper(settings)
        toolkit = build_research_toolkit(client=client, serper=serper)
        stats: dict[str, int] = {}
        cache = ToolCallCache()
        recorder = PerformanceRecorder()
        budget = ToolBudget()
        research_tools = build_research_tools(
            toolkit=toolkit,
            stats=stats,
            recorder=recorder,
            cache=cache,
            budget=budget,
            diagnostics_provider=diagnostics_provider,
        )
        prefetch = build_research_prefetcher(
            toolkit=toolkit, cache=cache, recorder=recorder, budget=budget, stats=stats
        )
        return JobResearchComponents(
            research_tools=research_tools,
            cache=cache,
            recorder=recorder,
            budget=budget,
            prefetch=prefetch,
            stats=stats,
        )

    return LiveComponentFactory(
        build_components=factory,
        diagnostics_factory=diagnostics_factory,
        set_job_id=set_job_id,
    )


class _PerJobFlowRunner:
    """P06-11G：Worker 侧「每 Job」FlowRunner 外壳。

    每次 ``run(request)`` 都通过 component_factory 新建一套 job-local 组件
    （ToolBudget/ToolCallCache/PerformanceRecorder/stats/research_tools/prefetch），
    再交给全新的 ``LiveResearchFlowRunner`` 执行——不在 Worker 启动时永久持有
    捕获旧预算/旧缓存的工具闭包，杜绝跨 Job 资源泄漏（真实故障根因）。

    P06-11K-5：component_factory 现在是 ``LiveComponentFactory``——
    ``run`` 前先 ``set_job_id`` 刷新诊断 job_id，再透传 ``diagnostics_factory``
    给 ``build_flow_runner``（生产路径工具摘要/LLM 摘要捕获全链路生效）。
    """

    def __init__(
        self,
        settings: Any,
        component_factory: LiveComponentFactory,
    ) -> None:
        self._settings = settings
        self._component_factory = component_factory
        # P06-06B：Worker 在 run 前注入的进度端口与 job_id（透传给当前 Job runner）
        self.progress: Any | None = None
        self.job_id: uuid.UUID | None = None
        self.last_state: Any | None = None

    def run(self, request: ResearchRequest) -> Any:
        from invest_research.infrastructure.flow_wiring import build_flow_runner

        # P06-11K-5：每次 run 前把 job_id 刷新进共享诊断 state（capture 的
        # buffer.job_id 与任务一致；tools provider 才能读到当前 Job 的 capture）。
        self._component_factory.set_job_id(self.job_id)
        runner = build_flow_runner(
            self._settings,
            component_factory=self._component_factory.build_components,
            diagnostics_factory=self._component_factory.diagnostics_factory,
        )
        runner.progress = self.progress
        runner.job_id = self.job_id
        state = runner.run(request)
        self.last_state = state
        return state


def _default_flow_runner() -> ResearchFlowRunner:
    """按 FLOW_MODE 环境变量构建 FlowRunner（默认 fake，不读 Settings/不依赖 key）。

    - ``FLOW_MODE=fake``（默认）：返回 ``ResearchFlowRunner``（P03 纯 fake 00-07 全链，
      不联网、不产生模型费用），保持 worker 模块导入零 Settings 依赖；
    - ``FLOW_MODE=live``：返回 ``_PerJobFlowRunner``——每次 run 用
      ``_build_live_component_factory`` 重建整套 job-local 组件并新建
      ``LiveResearchFlowRunner``（LLM/Serper Key 缺失或为空时 build 阶段 fail-fast）。
    """
    if os.environ.get("FLOW_MODE", "fake") == "fake":
        return ResearchFlowRunner()
    from invest_research.settings import get_settings

    settings = get_settings()
    return _PerJobFlowRunner(settings, _build_live_component_factory(settings))  # type: ignore[return-value]


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

    def _annual_runner(job_id: uuid.UUID, request: ResearchRequest) -> Any:
        """年度路径只使用 P07 节点表，不初始化 legacy workflow_steps。"""
        from invest_research.agents.llm_factory import LLMConfig
        from invest_research.application.annual_node_progress import AnnualNodeProgressService
        from invest_research.infrastructure.annual_company_facts_pipeline import (
            AnnualCompanyFactsArtifactPipeline,
        )
        from invest_research.infrastructure.annual_comparison_builder import AnnualComparisonBuilder
        from invest_research.infrastructure.annual_document_pipeline import (
            AnnualDocumentArtifactPipeline,
        )
        from invest_research.infrastructure.annual_evidence_fanout import (
            AnnualEvidenceFanoutPipeline,
        )
        from invest_research.infrastructure.annual_llm_fact_extraction import LLMFactExtractor
        from invest_research.infrastructure.annual_llm_writing import (
            AnnualLlmDispatcher,
            AnnualSectionExecutor,
        )
        from invest_research.infrastructure.annual_runtime import (
            AnnualResearchRuntime,
            AnnualRuntimeComponents,
        )
        from invest_research.infrastructure.db.annual_node_store import SqlAnnualNodeStore
        from invest_research.infrastructure.http.client import build_http_client
        from invest_research.infrastructure.real_tools import build_research_toolkit
        from invest_research.settings import get_settings

        settings = get_settings()
        client = build_http_client(
            connect_timeout=settings.http_connect_timeout,
            read_timeout=settings.http_read_timeout,
            user_agent=settings.http_user_agent
            or f"invest-research/0.1 (+{settings.sec_user_agent_contact})",
        )
        # 年度链路只使用 SEC 身份、submissions、facts 和 filing 下载器；网页搜索作为
        # 可选的叙事增强注入（Serper Key 缺失时优雅降级，不阻塞年度核心流程）。
        toolkit = build_research_toolkit(client=client, serper=None)
        artifact_root = Path(os.environ.get("ARTIFACT_ROOT", "artifacts"))
        document_pipeline = AnnualDocumentArtifactPipeline(artifact_root, toolkit.downloader)
        facts_pipeline = AnnualCompanyFactsArtifactPipeline(artifact_root, toolkit.facts)
        web_pipeline = _build_annual_web_search_pipeline(settings, client, artifact_root)
        # WS2.4：主动研究 Agent 生产注入（默认关闭；开启需 Serper Key，增加网页下载 I/O）。
        active_research = None
        if getattr(settings, "annual_active_research_enabled", False) and getattr(
            settings, "serper_api_key", None
        ):
            from invest_research.infrastructure.annual_active_research import ActiveResearchAgent
            from invest_research.tools.google_search import GoogleSearchTool
            from invest_research.tools.serper_adapter import SerperAdapter, SerperConfig

            try:
                serper = SerperAdapter(
                    client,
                    SerperConfig(
                        api_key=settings.serper_api_key,
                        endpoint=getattr(settings, "serper_endpoint", None),
                    ),
                )
                active_research = ActiveResearchAgent(
                    artifact_root=artifact_root,
                    completion=AnnualLlmDispatcher(LLMConfig.from_settings(settings)),
                    search_tool=GoogleSearchTool(provider=serper),
                    downloader=toolkit.downloader,
                )
            except Exception:  # noqa: BLE001 - 主动研究增强尽力而为，失败不阻塞核心流程
                active_research = None
        try:
            runtime = AnnualResearchRuntime(
                AnnualRuntimeComponents(
                    resolver=toolkit.resolver,
                    filings_fetcher=toolkit.submissions,
                    evidence_fanout=AnnualEvidenceFanoutPipeline(
                        document_pipeline,
                        facts_pipeline,
                        web_search_pipeline=web_pipeline,
                        artifact_root=artifact_root,
                    ),
                    comparison_builder=AnnualComparisonBuilder(
                        artifact_root,
                        fact_extractor=(
                            LLMFactExtractor(
                                AnnualLlmDispatcher(LLMConfig.from_settings(settings))
                            )
                            if getattr(settings, "annual_llm_extraction_enabled", False)
                            else None
                        ),
                    ),
                    artifact_root=artifact_root,
                    progress=AnnualNodeProgressService(SqlAnnualNodeStore(session_factory)),
                    section_executor=AnnualSectionExecutor(
                        artifact_root, AnnualLlmDispatcher(LLMConfig.from_settings(settings))
                    ),
                    active_research=active_research,
                    fact_extractor=(
                        LLMFactExtractor(AnnualLlmDispatcher(LLMConfig.from_settings(settings)))
                        if getattr(settings, "annual_llm_extraction_enabled", False)
                        else None
                    ),
                )
            )
            return runtime.run(job_id=job_id, request=request)
        finally:
            client.close()

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
            request = ResearchRequest(
                input_company=job.input_company,
                as_of_date=job.as_of_date,
                language=job.language,
                requested_forms=tuple(job.requested_forms),
                # P06-06A：把每个 Job 持久化的研究档位传给 FlowRunner
                research_profile=job.research_profile,
                research_mode=ResearchMode(job.research_mode),
            )
            if request.research_mode is ResearchMode.LEGACY:
                try:
                    progress.mark_step_succeeded(job_id, "00_request")
                except StepRecordError as exc:
                    _progress_logger().warning("标记 00_request 成功失败 job=%s: %s", job_id, exc)
            return request

    class _RepoWriter:
        def mark_running(self, job_id: uuid.UUID) -> bool:
            # 条件更新 pending→running 并记录 started_at（乐观锁防重复执行）
            with session_factory() as session:
                row = session.get(ResearchJobORM, job_id)
                if row is None or row.status != JobStatus.PENDING.value:
                    return False
                profile = row.research_profile or "deep"
                research_mode = ResearchMode(row.research_mode)
                row.status = JobStatus.RUNNING.value
                row.started_at = datetime.now(timezone.utc)
                session.commit()
            # P06-06B：幂等创建 00-07 步骤并标记 00_request running（重复投递不重复插入）
            if research_mode is ResearchMode.LEGACY:
                try:
                    progress.initialize_steps(job_id)
                    progress.mark_step_running(job_id, "00_request")
                except StepRecordError as exc:
                    _progress_logger().warning("进度初始化失败 job=%s: %s", job_id, exc)
            # P06-06C：条件转换成功（返回 True）才计数 running（重复投递不会重复计数）
            from invest_research.infrastructure.observability.metrics_events import (
                count_research_job,
                set_research_job_in_progress,
            )

            count_research_job("running")
            # P06-09C：只在条件转换成功后按档位设置执行中 Gauge（重复投递不重复增减）
            set_research_job_in_progress(profile, 1)
            return True

        def mark_succeeded(self, job_id: uuid.UUID) -> None:
            with session_factory() as session:
                row = session.get(ResearchJobORM, job_id)
                if row is None:
                    return
                profile = row.research_profile or "deep"
                started_at = row.started_at
                row.status = JobStatus.SUCCEEDED.value
                row.completed_at = datetime.now(timezone.utc)
                row.current_step = None  # P06-06B：终态清空 current_step
                session.commit()
            # P06-06C：真实到达终态才计数 succeeded（重复投递不重复计数）
            from invest_research.infrastructure.observability.metrics_events import (
                count_research_job,
                observe_research_job,
                set_research_job_in_progress,
            )

            count_research_job("succeeded")
            # P06-09C：成功终态记录真实总耗时 + 释放执行中 Gauge
            if started_at is not None:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                duration = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)
                observe_research_job(profile, "succeeded", duration)
            set_research_job_in_progress(profile, -1)

        def mark_failed(
            self,
            job_id: uuid.UUID,
            *,
            error_code: str = "INTERNAL_BUG",
            error_message: str = "",
            failure_stage: str | None = None,
        ) -> None:
            # P05.5-deploy-fix：Flow 异常 → running → failed（防止任务永久卡 running）
            # P06-09：保存稳定错误码/脱敏消息/失败阶段，供 failed Job 展示与审计。
            sanitized_message = (error_message or "")[:500]
            with session_factory() as session:
                row = session.get(ResearchJobORM, job_id)
                if row is None:
                    return
                profile = row.research_profile or "deep"
                is_legacy = row.research_mode == ResearchMode.LEGACY.value
                started_at = row.started_at
                row.status = JobStatus.FAILED.value
                row.completed_at = datetime.now(timezone.utc)
                row.current_step = None  # P06-06B：终态清空 current_step
                row.error_code = error_code
                row.error_message = sanitized_message or None
                row.failure_stage = failure_stage
                session.commit()
            # P06-06B：收口所有仍为 running 的步骤为 failed_terminal（不留虚假 running）
            if is_legacy:
                try:
                    progress.fail_all_running_steps(
                        job_id,
                        error_code=error_code,
                        error_message=sanitized_message or "任务执行失败，流程异常终止",
                    )
                except StepRecordError as exc:
                    _progress_logger().warning("收口 running 步骤失败 job=%s: %s", job_id, exc)
            # P06-06C：真实到达终态才计数 failed（重复执行不会重复计数）
            from invest_research.infrastructure.observability.metrics_events import (
                count_failure,
                count_research_job,
                observe_research_job,
                set_research_job_in_progress,
            )

            count_research_job("failed")
            # P06-09C：失败终态记录真实总耗时 + 释放执行中 Gauge + 失败分类
            if started_at is not None:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                duration = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)
                observe_research_job(profile, "failed", duration)
            set_research_job_in_progress(profile, -1)
            count_failure(failure_stage or "unknown", error_code)

    legacy_runner = flow_runner if flow_runner is not None else _default_flow_runner()

    class _ModeDispatchingRunner:
        progress: Any | None = None
        job_id: uuid.UUID | None = None
        last_state: Any | None = None

        def run(self, request: ResearchRequest) -> Any:
            if request.research_mode is ResearchMode.ANNUAL_DEEP:
                if self.job_id is None:
                    raise RuntimeError("annual_deep 运行缺少 job_id")
                self.last_state = _annual_runner(self.job_id, request)
                return self.last_state
            legacy_runner.progress = self.progress
            legacy_runner.job_id = self.job_id
            self.last_state = legacy_runner.run(request)
            return self.last_state

    runner = _ModeDispatchingRunner()
    # P06-07 前置修复：Worker 成功获得 Flow state 后发布最终报告
    # （fake/live 共用确定性服务；发布失败 → mark_failed，不误报完整发布成功）。
    artifact_root = os.environ.get("ARTIFACT_ROOT", "artifacts")
    report_publisher = ReportArtifactPublisher(artifact_root)
    service = ExecuteResearchJobService(
        loader=_RepoLoader(),
        writer=_RepoWriter(),
        flow_runner=runner,
        report_publisher=report_publisher,
    )

    # P05.5-opt：成功执行后把步骤/工件/耗时落库（失败只告警，不回滚已成功任务）
    recorder = ExecutionRecorder(session_factory, artifact_root)

    def _record_execution(job_id: uuid.UUID) -> None:
        try:
            recorder.record(job_id, getattr(runner, "last_state", None))
            _record_job_perf_metrics(getattr(runner, "last_state", None))
        except Exception as exc:  # noqa: BLE001 - 记录失败不影响任务结果
            _progress_logger().warning("执行记录落库失败 job=%s: %s", job_id, exc)

    return ResearchJobExecutionHandler(service, recorder=_record_execution)


def _record_job_perf_metrics(state: Any) -> None:
    """WS3：把已发布任务的 token/工具调用/成本写入聚合指标（best-effort，终态一次）。

    只读 state.run_manifest；缺失 token 不伪造 0；成本按 PRICING_FILE 估算（未配置不计算）。
    """
    if state is None or getattr(state, "run_manifest", None) is None:
        return
    from invest_research.infrastructure.observability.metrics_events import record_job_performance

    manifest = state.run_manifest
    perf = manifest.get("performance") or {}
    usage = perf.get("token_usage") or {}
    inv = (manifest.get("evidence") or {}).get("invocation_summary") or {}
    request = getattr(state, "request", None)
    profile = getattr(request, "research_profile", None) or "deep"
    mode = str(manifest.get("research_mode") or "legacy")
    status = str(manifest.get("finalization_status") or "published")
    tool_calls = sum(int(v) for v in inv.values() if isinstance(v, (int, float)))
    cost = None
    pricing_file = os.environ.get("PRICING_FILE")
    if pricing_file:
        import invest_research.application.costing as costing

        pricing = costing.load_pricing(pricing_file)
        entry = costing.pricing_entry_for(pricing, profile)
        cost = costing.estimate_job_cost(
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            pricing_entry=entry,
        )
    record_job_performance(
        profile=str(profile),
        mode=mode,
        status=status,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        tool_calls=tool_calls,
        cost_usd=cost,
    )


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


def _run_stale_job_recovery() -> None:
    """Worker 启动时收口历史 stale running Job（P06-09）。

    语义（不删除记录/工件）：
    - 把所有仍为 ``running`` 的 Job 条件更新为 ``failed``，并记录
      error_code=STALE_RUNNING_RECOVERED（脱敏消息 + failure_stage=startup_recovery）；
    - 对每个被收口的 Job，调用 SqlProgressSink.fail_all_running_steps 把步骤
      收口为 failed_terminal（不留虚假 running）；
    - 只在 DB 可用时执行；任何失败只告警，不影响 worker 启动。
    """
    try:
        from sqlalchemy import select

        session_factory = _build_session_factory()
        with session_factory() as session:
            stale = (
                session.execute(
                    select(ResearchJobORM).where(ResearchJobORM.status == JobStatus.RUNNING.value)
                )
                .scalars()
                .all()
            )
            # session.commit() 默认会 expire ORM 实例；后续步骤收口只能使用
            # 已提取的稳定标识，不能再访问 detached ``ResearchJob`` 属性。
            stale_job_ids = [job.id for job in stale]
            now = datetime.now(timezone.utc)
            for job in stale:
                job.status = JobStatus.FAILED.value
                job.completed_at = now
                job.current_step = None
                job.error_code = "STALE_RUNNING_RECOVERED"
                job.error_message = "任务在 Worker 重启时仍处于 running，已由启动恢复收口为 failed"
                job.failure_stage = "startup_recovery"
            session.commit()
        from invest_research.infrastructure.observability.metrics_events import (
            count_stale_recovery,
        )

        count_stale_recovery("recovered" if stale_job_ids else "none")
        progress = SqlProgressSink(_build_session_factory())
        for job_id in stale_job_ids:
            try:
                progress.fail_all_running_steps(
                    job_id,
                    error_code="STALE_RUNNING_RECOVERED",
                    error_message="任务在 Worker 重启时仍处于 running，已由启动恢复收口为 failed",
                )
            except StepRecordError as exc:
                _progress_logger().warning("启动恢复收口步骤失败 job=%s: %s", job_id, exc)
    except Exception:  # noqa: BLE001 - 恢复尽力而为，不阻塞 worker 启动
        _progress_logger().warning("启动 stale running Job 恢复失败（继续启动）", exc_info=True)


def _build_celery_app() -> Celery:
    """构建 Celery app：broker 取环境变量 BROKER_URL，缺省 memory://。

    P06-09：构建前先执行一次 stale running Job 启动恢复（不删除记录/工件），
    保证历史卡 running 的任务被收口到 failed，不再留在 running。
    """
    _run_stale_job_recovery()
    broker = os.environ.get("BROKER_URL", "memory://")
    _setup_otel_from_env()
    app = create_celery_app(broker_url=broker)
    register_tasks(app, _build_handler())
    # P06-06C：worker_init 启动父进程 HTTP metrics 端点（9101）；
    # worker_process_shutdown 删除子进程 .db 文件。
    from invest_research.infrastructure.observability.worker_metrics_server import (
        install_worker_signals,
    )

    install_worker_signals(app)
    return app


celery_app: Celery = _build_celery_app()
