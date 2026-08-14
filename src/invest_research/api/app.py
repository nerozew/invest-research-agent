"""FastAPI application factory（P04-01 + P04-02）。

原则：
- 模块导入时不连接数据库或 Redis；只有显式调用 ``create_app`` 且未注入
  ``health_checker`` 时，才会由 ``build_health_checker`` 惰性创建探测资源。
- 依赖注入：``/readiness`` 通过 ``Depends(_get_health_checker)`` 从
  ``request.app.state`` 获取 checker；``POST /v1/research-jobs`` 通过
  ``Depends(_get_job_service)`` 获取 job service。路由不直接实例化具体实现，
  也不包含 Flow 业务逻辑（只做 HTTP 转换）。
- 响应模型由 ``health.py`` / ``jobs.py`` 定义，是 API 层的公开 DTO。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import Depends, FastAPI, Header, Query, Request, Response, status
from fastapi.responses import JSONResponse

from invest_research.api.health import (
    DependencyHealthChecker,
    DependencyStatus,
    HealthChecker,
    HealthResponse,
    ReadinessResponse,
    build_health_checker,
    dispose_dependency_resources,
)
from invest_research.api.jobs import (
    CreateResearchJobRequest,
    CreateResearchJobResponse,
    GetResearchJobResponse,
)
from invest_research.application.artifacts import (
    ArtifactCatalogStore,
    ArtifactContentStore,
    ArtifactInfo,
    GetJobArtifactContentService,
    GetJobArtifactsService,
    InvalidArtifactKey,
)
from invest_research.application.cancellation import (
    CancelResearchJobService,
    CancelStatusWriter,
)
from invest_research.application.idempotency import (
    CreateResearchJobIdempotentService,
    IdempotencyConflict,
    IdempotencyStore,
)
from invest_research.application.job_dispatcher import JobDispatcher
from invest_research.application.job_listing import (
    InvalidJobListCursor,
    JobListPage,
    JobListStore,
    ListResearchJobService,
)
from invest_research.application.jobs import (
    CreatedJob,
    CreateResearchJobService,
    GetResearchJobService,
    JobQueryStore,
    JobSnapshot,
    JobStore,
)
from invest_research.domain.status import JobStatus
from invest_research.settings import Settings, get_settings

__all__ = ["create_app"]


def _get_health_checker(request: Request) -> HealthChecker:
    """FastAPI 依赖：从 ``app.state`` 取出注入的 checker。

    路由只依赖 ``HealthChecker`` 协议，不直接耦合 SQLAlchemy/Redis；
    application factory 在创建 app 时把具体实现放到 ``app.state``。
    """
    # app.state 是任意属性容器，类型为 Any；显式断言为 HealthChecker，
    # 满足 mypy strict 的 no-any-return 约束。
    return cast(HealthChecker, request.app.state.health_checker)


def _get_job_query_service(request: Request) -> GetResearchJobService | None:
    """FastAPI 依赖：取出注入的任务查询 service（未注入时返回 None）。"""
    return cast(GetResearchJobService | None, request.app.state.job_query_service)


def _get_job_service(request: Request) -> CreateResearchJobService | None:
    """FastAPI 依赖：从 ``app.state`` 取出注入的 job service。

    与 health checker 相同的注入模式：factory 通过构造参数注入，
    路由只依赖 service 公开接口，不接触存储实现。
    未注入存储时返回 None（创建任务接口返回 503）。
    """
    return cast(CreateResearchJobService | None, request.app.state.job_service)


def create_app(
    *,
    settings: Settings | None = None,
    health_checker: HealthChecker | None = None,
    job_store: JobStore | None = None,
    job_query_store: JobQueryStore | None = None,
    job_list_store: JobListStore | None = None,
    artifact_catalog_store: ArtifactCatalogStore | None = None,
    artifact_content_store: ArtifactContentStore | None = None,
    idempotency_store: IdempotencyStore | None = None,
    cancel_status_writer: CancelStatusWriter | None = None,
    job_dispatcher: JobDispatcher | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例（application factory）。

    - ``settings``：缺省使用全局 ``get_settings()``，保持与现有配置单一来源。
    - ``health_checker``：缺省时由 ``build_health_checker(settings)`` 创建默认
      实现（惰性创建带显式超时的 engine/Redis 客户端），并在 lifespan 退出时
      释放；测试可注入 fake checker，此时 app 全程不创建任何真实依赖资源。
    - ``job_store``：创建投研任务的持久化端口（P04-02）。注入后用于
      ``POST /v1/research-jobs``；缺省 ``None`` 表示"未连接存储"
      （此时创建任务接口返回 503），保持模块导入零数据库连接。
    """
    resolved_settings = settings or get_settings()
    owns_checker = health_checker is None
    checker = health_checker or build_health_checker(resolved_settings)
    job_service = CreateResearchJobService(store=job_store) if job_store is not None else None
    job_query_service = (
        GetResearchJobService(store=job_query_store) if job_query_store is not None else None
    )
    job_list_service = (
        ListResearchJobService(store=job_list_store) if job_list_store is not None else None
    )
    artifact_catalog_service = (
        GetJobArtifactsService(catalog=artifact_catalog_store)
        if artifact_catalog_store is not None
        else None
    )
    artifact_content_service = (
        GetJobArtifactContentService(content_store=artifact_content_store)
        if artifact_content_store is not None
        else None
    )
    cancel_job_service = (
        CancelResearchJobService(writer=cancel_status_writer)
        if cancel_status_writer is not None
        else None
    )
    idempotent_job_service = (
        CreateResearchJobIdempotentService(
            job_service=job_service,
            idempotency_store=idempotency_store,
        )
        if job_service is not None and idempotency_store is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # 只释放 factory 自己创建的默认资源；注入的 checker 由调用方管理。
        if owns_checker and isinstance(checker, DependencyHealthChecker):
            dispose_dependency_resources(checker)

    app = FastAPI(
        title=resolved_settings.project_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.health_checker = checker
    app.state.job_service = job_service
    app.state.job_query_service = job_query_service
    app.state.job_list_service = job_list_service
    app.state.artifact_catalog_service = artifact_catalog_service
    app.state.artifact_content_service = artifact_content_service
    app.state.cancel_job_service = cancel_job_service
    app.state.idempotent_job_service = idempotent_job_service
    app.state.job_dispatcher = job_dispatcher

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["ops"],
        summary="Liveness 探针",
        description="只判断 API 进程是否存活；不访问数据库或 Redis。",
    )
    def health() -> HealthResponse:
        return HealthResponse(service=resolved_settings.project_name)

    @app.get(
        "/readiness",
        response_model=ReadinessResponse,
        tags=["ops"],
        summary="Readiness 探针",
        description=(
            "分别检查 PostgreSQL 与 Redis 的就绪状态；"
            "任一依赖不可用时返回 503，并分别报告每个依赖的状态。"
        ),
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
    )
    def readiness(
        checker: HealthChecker = Depends(_get_health_checker),
    ) -> Response:
        db_result = checker.check_database()
        redis_result = checker.check_redis()

        database = DependencyStatus(status=db_result["status"], error_code=db_result["error_code"])
        redis = DependencyStatus(
            status=redis_result["status"], error_code=redis_result["error_code"]
        )
        ready = database.status == "ok" and redis.status == "ok"

        payload = ReadinessResponse(
            status="ready" if ready else "not_ready",
            ready=ready,
            database=database,
            redis=redis,
        )
        if ready:
            return JSONResponse(status_code=status.HTTP_200_OK, content=payload.model_dump())
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(),
        )

    @app.get(
        "/v1/research-jobs/{job_id}",
        response_model=GetResearchJobResponse,
        tags=["jobs"],
        summary="查询投研任务状态",
        description=(
            "按 job_id 查询任务状态、当前步骤、步骤耗时、重试次数、错误码与耗时（FR-013）。"
            "任务不存在返回 404；job_id 非法返回 422。"
        ),
        responses={
            status.HTTP_200_OK: {"model": GetResearchJobResponse},
            status.HTTP_404_NOT_FOUND: {"description": "任务不存在"},
        },
    )
    def get_job(
        job_id: uuid.UUID,
        query_service: GetResearchJobService | None = Depends(_get_job_query_service),
    ) -> Response:
        if query_service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "任务查询存储未连接，无法查询任务"},
            )
        snapshot: JobSnapshot | None = query_service.get(job_id)
        if snapshot is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "任务不存在"},
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=snapshot.model_dump(mode="json"),
        )

    def _get_job_list_service(request: Request) -> ListResearchJobService | None:
        return cast(ListResearchJobService | None, request.app.state.job_list_service)

    @app.get(
        "/v1/research-jobs",
        response_model=JobListPage,
        tags=["jobs"],
        summary="任务列表（最近任务）",
        description=(
            "按 created_at 倒序稳定分页列出任务（created_at 相同时用 job_id 打破平局）。"
            "支持 limit（1~100，默认 20）、status 过滤与 cursor 分页；"
            "不暴露 config_snapshot、数据库 URL 或内部路径。"
        ),
        responses={
            status.HTTP_200_OK: {"model": JobListPage},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "任务列表存储未连接"},
        },
    )
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        status_filter: JobStatus | None = Query(default=None, alias="status"),
        cursor: str | None = Query(default=None),
        list_service: ListResearchJobService | None = Depends(_get_job_list_service),
    ) -> Response:
        if list_service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "任务列表存储未连接，无法列出任务"},
            )
        try:
            page = list_service.list_jobs(status=status_filter, limit=limit, cursor=cursor)
        except InvalidJobListCursor:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"detail": "cursor 非法"},
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=page.model_dump(mode="json"),
        )

    def _get_idempotent_job_service(
        request: Request,
    ) -> CreateResearchJobIdempotentService | None:
        return cast(
            CreateResearchJobIdempotentService | None,
            request.app.state.idempotent_job_service,
        )

    def _get_job_dispatcher(request: Request) -> JobDispatcher | None:
        return cast(JobDispatcher | None, request.app.state.job_dispatcher)

    def _get_artifact_catalog_service(request: Request) -> GetJobArtifactsService | None:
        return cast(GetJobArtifactsService | None, request.app.state.artifact_catalog_service)

    def _get_artifact_content_service(request: Request) -> GetJobArtifactContentService | None:
        return cast(
            GetJobArtifactContentService | None,
            request.app.state.artifact_content_service,
        )

    @app.get(
        "/v1/research-jobs/{job_id}/artifacts",
        response_model=list[ArtifactInfo],
        tags=["artifacts"],
        summary="工件清单",
        description="列出该 job 已登记的工件（FR-009 中间产出可见性）。",
        responses={
            status.HTTP_200_OK: {"model": list[ArtifactInfo]},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "工件目录未连接"},
        },
    )
    def list_artifacts(
        job_id: uuid.UUID,
        catalog_service: GetJobArtifactsService | None = Depends(_get_artifact_catalog_service),
    ) -> Response:
        if catalog_service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "工件目录未连接，无法列出工件"},
            )
        artifacts = catalog_service.list(job_id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=[a.model_dump(mode="json") for a in artifacts],
        )

    @app.get(
        "/v1/research-jobs/{job_id}/artifacts/{artifact_key:path}",
        tags=["artifacts"],
        summary="下载已登记工件",
        description=(
            "仅允许下载该 job 已登记的工件；key 含路径穿越/危险字符返回 400，"
            "不存在或不属于该 job 返回 404。"
        ),
        responses={
            status.HTTP_200_OK: {"content": {"application/octet-stream": {}}},
            status.HTTP_400_BAD_REQUEST: {"description": "artifact_key 非法"},
            status.HTTP_404_NOT_FOUND: {"description": "工件不存在"},
        },
    )
    def download_artifact(
        job_id: uuid.UUID,
        artifact_key: str,
        content_service: GetJobArtifactContentService | None = Depends(
            _get_artifact_content_service
        ),
    ) -> Response:
        if content_service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "工件存储未连接，无法下载"},
            )
        try:
            content = content_service.read(job_id, artifact_key)
        except InvalidArtifactKey as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(exc)},
            )
        if content is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "工件不存在"},
            )
        return Response(
            content=content,
            media_type="application/octet-stream",
        )

    @app.post(
        "/v1/research-jobs",
        response_model=CreateResearchJobResponse,
        tags=["jobs"],
        summary="创建投研任务",
        description=(
            "接受公司名称或 ticker、as_of_date、语言与表单类型（FR-001）。"
            "成功返回 HTTP 202 + job_id；请求体非法时返回 422。"
        ),
        responses={
            status.HTTP_202_ACCEPTED: {"model": CreateResearchJobResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "任务存储未连接"},
        },
    )
    def create_job(
        body: CreateResearchJobRequest,
        service: CreateResearchJobService | None = Depends(_get_job_service),
        idempotent_service: CreateResearchJobIdempotentService | None = Depends(
            _get_idempotent_job_service
        ),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        dispatcher: JobDispatcher | None = Depends(_get_job_dispatcher),
    ) -> Response:
        if service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "任务存储未连接，无法创建任务"},
            )
        # 幂等路径：提供了 Idempotency-Key 且已注入幂等池时启用。
        if idempotency_key is not None and idempotent_service is not None:
            try:
                stored, created_now = idempotent_service.create(body, idempotency_key)
            except IdempotencyConflict as exc:
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"detail": str(exc)},
                )
            # 仅首次创建成功后投递；幂等复用旧 job_id 时不重复投递。
            if dispatcher is not None and created_now:
                dispatcher.dispatch(stored.job_id)
            payload = CreateResearchJobResponse(job_id=stored.job_id, status=stored.status)
            status_code = status.HTTP_202_ACCEPTED if created_now else status.HTTP_200_OK
            return JSONResponse(
                status_code=status_code,
                content=payload.model_dump(mode="json"),
            )
        created: CreatedJob = service.create(body)
        # 非幂等路径也投递（首次创建成功）。
        if dispatcher is not None:
            dispatcher.dispatch(created.job_id)
        payload = CreateResearchJobResponse(job_id=created.job_id, status=created.status)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=payload.model_dump(mode="json"),
        )

    def _get_cancel_job_service(request: Request) -> CancelResearchJobService | None:
        return cast(CancelResearchJobService | None, request.app.state.cancel_job_service)

    @app.delete(
        "/v1/research-jobs/{job_id}",
        tags=["jobs"],
        summary="取消投研任务",
        description=(
            "协作式取消：仅 pending/running 可取消为 cancelled（FR-015）。"
            "终态任务不可取消；重复取消保持幂等返回 200。"
            "取消存储未连接返回 503。"
        ),
        responses={
            status.HTTP_200_OK: {"description": "已取消"},
            status.HTTP_404_NOT_FOUND: {"description": "任务不存在"},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "取消存储未连接"},
        },
    )
    def cancel_job(
        job_id: uuid.UUID,
        cancel_service: CancelResearchJobService | None = Depends(_get_cancel_job_service),
    ) -> Response:
        if cancel_service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "取消存储未连接，无法取消任务"},
            )
        did_cancel, already_cancelled = cancel_service.cancel(job_id)
        if did_cancel:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"job_id": str(job_id), "status": "cancelled", "did_cancel": True},
            )
        # 幂等：已是终态/已取消
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "job_id": str(job_id),
                "status": "cancelled",
                "did_cancel": False,
                "already_cancelled": already_cancelled,
            },
        )

    return app
