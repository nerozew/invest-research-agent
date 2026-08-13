"""前端 typed API client（P04-UI-01）。

只通过 HTTP 调用 FastAPI（`docs/02-ARCHITECTURE.md §11`：Streamlit 是
FastAPI 的 HTTP 客户端）。职责：
- 显式设置全部四个 httpx 超时参数（connect/read/write/pool），杜绝"无限等待"；
- 把后端响应解析为 typed DTO（``frontend.models``）；
- 安全分类 4xx / 5xx / 网络 / 超时错误（``frontend.errors``），
  错误消息只透出后端 ``detail``，不显示连接串/密钥/内部路径。

可测试性：构造时通过 ``transport`` 参数注入 ``httpx.MockTransport``，
测试全程 fake HTTP，不依赖真实数据库、Redis、Docker 或模型。
"""

from __future__ import annotations

from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from invest_research.frontend.errors import (
    ApiNetworkError,
    ApiTimeoutError,
    classify_response_error,
)
from invest_research.frontend.models import (
    ArtifactInfo,
    CreateResearchJobRequest,
    CreateResearchJobResponse,
    HealthResponse,
    JobSnapshot,
    ReadinessResponse,
)

__all__ = ["ResearchApiClient"]

# 默认并发/连接上限（与共享 HTTP client 的工程决策一致：显式、有界）。
_DEFAULT_MAX_CONNECTIONS = 10

# 泛型：GET 解析的目标模型（Pydantic BaseModel 子类）。
ModelT = TypeVar("ModelT", bound=BaseModel)


class ResearchApiClient:
    """FastAPI 的 typed HTTP 客户端。

    参数：
    - ``base_url``：后端地址（如 ``http://localhost:8000``），生产由环境变量配置。
    - ``timeout``：请求总超时秒数（读写显式一致）。
    - ``transport``：可注入的 httpx transport（测试用 ``httpx.MockTransport``）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url 不能为空")
        self._base_url = base_url.rstrip("/")
        # 显式设置全部四个超时参数：connect/read/write/pool。
        # 默认写/池超时与读超时一致，避免 httpx 因参数缺失抛 ValueError。
        self._timeout = httpx.Timeout(
            timeout, connect=timeout, read=timeout, write=timeout, pool=timeout
        )
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=transport,
            limits=httpx.Limits(max_connections=_DEFAULT_MAX_CONNECTIONS),
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Liveness / Readiness（P04-01 后端接口）
    # ------------------------------------------------------------------

    def health(self) -> HealthResponse:
        """GET /health：只表示 API 进程存活。"""
        return self._get_model("/health", HealthResponse)

    def readiness(self) -> ReadinessResponse:
        """GET /readiness：分别报告 database / redis 就绪状态。

        后端在依赖不可用时返回 503，此时响应体仍是合法的
        ``ReadinessResponse``（``ready=False``）。因此把 503 视为
        "可解析的就绪状态"，交给 UI 展示而不是抛错。
        """
        resp = self._request("GET", "/readiness", ok_statuses={503})
        payload = self._decode_json(resp)
        if payload is None or not isinstance(payload, dict):
            raise ApiTimeoutError("readiness 响应格式非法")
        return ReadinessResponse.model_validate(payload)

    # ------------------------------------------------------------------
    # 创建任务（P04-02 + P04-05：Idempotency-Key）
    # ------------------------------------------------------------------

    def create_research_job(
        self,
        *,
        request: CreateResearchJobRequest,
        idempotency_key: str,
    ) -> CreateResearchJobResponse:
        """POST /v1/research-jobs：携带 Idempotency-Key 创建任务。

        202：新建任务；200：同一 key 复用已有任务。
        409（同 key 不同请求体）抛 ``HttpStatusError``，UI 提示冲突。
        """
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key 不能为空")
        resp = self._request(
            "POST",
            "/v1/research-jobs",
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key.strip()},
        )
        payload = self._decode_json(resp)
        if payload is None or not isinstance(payload, dict):
            raise ApiTimeoutError("create 响应格式非法")
        return CreateResearchJobResponse.model_validate(payload)

    # ------------------------------------------------------------------
    # 查询任务状态（P04-03）
    # ------------------------------------------------------------------

    def get_research_job(self, job_id: str) -> JobSnapshot:
        """GET /v1/research-jobs/{id}：查询任务状态与步骤快照。

        404（任务不存在）抛 ``ApiNotFoundError``。
        """
        resp = self._request("GET", f"/v1/research-jobs/{job_id}")
        payload = self._decode_json(resp)
        if payload is None or not isinstance(payload, dict):
            raise ApiTimeoutError("get job 响应格式非法")
        return JobSnapshot.model_validate(payload)

    # ------------------------------------------------------------------
    # 工件清单与下载（P04-04 后端接口）
    # ------------------------------------------------------------------

    def list_artifacts(self, job_id: str) -> list[ArtifactInfo]:
        """GET /v1/research-jobs/{id}/artifacts：列出已登记工件。

        工件 storage_uri 是服务器内部路径，前端不展示；只列出 key/type/size。
        """
        resp = self._request("GET", f"/v1/research-jobs/{job_id}/artifacts")
        payload = self._decode_json(resp)
        if payload is None or not isinstance(payload, list):
            raise ApiTimeoutError("artifacts 响应格式非法")
        return [ArtifactInfo.model_validate(item) for item in payload]

    def download_artifact(self, job_id: str, artifact_key: str) -> bytes:
        """GET /v1/research-jobs/{id}/artifacts/{key}：下载已登记工件字节。

        只允许该 job 已登记的工件（后端路径穿越防护）；不存在抛 ApiNotFoundError。
        """
        resp = self._request("GET", f"/v1/research-jobs/{job_id}/artifacts/{artifact_key}")
        return resp.content

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _get_model(self, path: str, model_cls: type[ModelT]) -> ModelT:
        """GET 并解析为 typed 模型（供 health 等接口使用）。"""
        resp = self._request("GET", path)
        payload = self._decode_json(resp)
        if payload is None or not isinstance(payload, dict):
            raise ApiTimeoutError(f"{path} 响应格式非法")
        return model_cls.model_validate(payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        ok_statuses: set[int] | None = None,
    ) -> httpx.Response:
        """发送请求并统一错误分类。

        - 2xx（或 ``ok_statuses`` 中列明的状态码）正常返回；
        - 其他 4xx/5xx 分类为 ``ApiError`` 子类抛出；
        - 超时/网络错误归为 ``ApiTimeoutError`` / ``ApiNetworkError``。
        """
        try:
            resp = self._client.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise ApiTimeoutError("请求超时（请检查 API 地址与网络）") from exc
        except httpx.TransportError as exc:
            raise ApiNetworkError("无法连接后端 API（请检查 API 地址与网络）") from exc

        if resp.status_code >= 400 and not (
            ok_statuses is not None and resp.status_code in ok_statuses
        ):
            raise classify_response_error(resp.status_code, self._decode_json(resp))
        return resp

    @staticmethod
    def _decode_json(resp: httpx.Response) -> Any:
        """安全解码 JSON；非法 JSON 返回 None（调用方判定格式错误）。"""
        try:
            return resp.json()
        except ValueError:
            return None
