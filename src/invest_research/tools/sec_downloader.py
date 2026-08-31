"""FilingDownloaderTool（P02-08）：安全下载 SEC 文件（大小/类型/checksum 校验）。"""

from __future__ import annotations

import hashlib

import httpx
from pydantic import BaseModel, Field, field_validator

from invest_research.domain.errors import ErrorCode
from invest_research.infrastructure.http.client import classify_http_exception, classify_status_code
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset({"text/html", "application/pdf"})


class DownloadRequest(BaseModel):
    model_config = {"frozen": True}

    url: str = Field(min_length=1, max_length=2048)
    max_bytes: int = Field(default=DEFAULT_MAX_BYTES, ge=1)

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url 不能为空")
        return v


class DownloadedDocument(BaseModel):
    model_config = {"frozen": True}

    content: bytes
    media_type: str
    byte_size: int
    content_checksum: str  # sha256 hex


class SECDownloaderTool:
    """安全下载：注入 client；校验大小/媒体类型/内容 checksum。"""

    name = "sec_downloader"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def execute(self, request: DownloadRequest) -> ToolResult[DownloadedDocument]:
        try:
            response = self._client.get(request.url)
        except httpx.HTTPError as exc:
            return ToolFailure(
                error=ToolError(error_code=classify_http_exception(exc), message=f"下载失败: {exc}")
            )

        if response.is_error:
            code = classify_status_code(response.status_code)
            return ToolFailure(
                error=ToolError(
                    error_code=code if code else ErrorCode.INTERNAL_BUG,
                    message=f"HTTP {response.status_code}",
                )
            )

        content = response.content
        if len(content) > request.max_bytes:
            return ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.INPUT_INVALID,
                    message=f"文件过大: {len(content)} > {request.max_bytes}",
                )
            )

        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type not in ALLOWED_MEDIA_TYPES:
            return ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.DOCUMENT_UNSUPPORTED,
                    message=f"不支持的媒体类型: {media_type}",
                )
            )

        return ToolSuccess(
            value=DownloadedDocument(
                content=content,
                media_type=media_type,
                byte_size=len(content),
                content_checksum=hashlib.sha256(content).hexdigest(),
            )
        )
