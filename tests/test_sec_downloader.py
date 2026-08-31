"""P02-08 SECDownloaderTool 测试（MockTransport，不发起真实网络）。"""

from __future__ import annotations

import hashlib

import httpx

from invest_research.domain.errors import ErrorCode
from invest_research.tools.base import ToolFailure, ToolSuccess
from invest_research.tools.sec_downloader import DownloadRequest, SECDownloaderTool


def _mock_client(
    content: bytes = b"<html>ok</html>", media_type: str = "text/html", status: int = 200
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, content=content, headers={"Content-Type": media_type}, request=request
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_success_with_checksum() -> None:
    """成功下载：返回内容、大小、sha256 checksum。"""
    body = b"<html>SEC filing</html>"
    tool = SECDownloaderTool(_mock_client(body))
    result = tool.execute(DownloadRequest(url="https://www.sec.gov/a.htm"))

    assert isinstance(result, ToolSuccess)
    doc = result.value
    assert doc.content == body
    assert doc.byte_size == len(body)
    assert doc.content_checksum == hashlib.sha256(body).hexdigest()
    assert doc.media_type == "text/html"


def test_oversize_rejected() -> None:
    """超过大小上限 → INPUT_INVALID 失败。"""
    tool = SECDownloaderTool(_mock_client(b"x" * 100))
    result = tool.execute(DownloadRequest(url="https://x/a", max_bytes=10))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INPUT_INVALID


def test_unsupported_media_type_rejected() -> None:
    """非白名单媒体类型 → DOCUMENT_UNSUPPORTED 失败。"""
    tool = SECDownloaderTool(_mock_client(b"x", media_type="application/octet-stream"))
    result = tool.execute(DownloadRequest(url="https://x/a"))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.DOCUMENT_UNSUPPORTED


def test_http_error_maps_to_failure() -> None:
    """429 → RATE_LIMITED（可重试）。"""
    tool = SECDownloaderTool(_mock_client(status=429))
    result = tool.execute(DownloadRequest(url="https://x/a"))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.RATE_LIMITED
