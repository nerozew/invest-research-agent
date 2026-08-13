"""P02-05 SECSubmissionsTool 测试（recorded fixture + MockTransport，不发起真实网络）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from invest_research.domain.errors import ErrorCode
from invest_research.tools.base import ToolFailure, ToolSuccess
from invest_research.tools.sec_submissions import (
    FetchSubmissionsRequest,
    SECSubmissionsTool,
    build_document_url,
    build_submissions_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sec_submissions_msft.json"


def _mock_client(payload: dict[str, Any], status: int = 200) -> httpx.Client:
    """构造 MockTransport client：返回 fixture JSON 或指定状态。"""
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def msft_payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_build_urls() -> None:
    """submissions 端点与 primary document URL 构造正确（CIK 去前导零、accession 去连字符）。"""
    assert build_submissions_url("0000789019") == "https://data.sec.gov/submissions/0000789019.json"
    assert (
        build_document_url("0000789019", "0000950170-25-000009", "msft-20250630.htm")
        == "https://www.sec.gov/Archives/edgar/data/789019/000095017025000009/msft-20250630.htm"
    )


def test_as_of_date_filters_recent_filings(msft_payload: dict[str, Any]) -> None:
    """截止日过滤：2026-01-29 保留 10-K/10-Q 共 3 条；排除 8-K 与未来申报。"""
    tool = SECSubmissionsTool(_mock_client(msft_payload))
    result = tool.execute(FetchSubmissionsRequest(cik="0000789019", as_of_date=date(2026, 1, 29)))

    assert isinstance(result, ToolSuccess)
    filings = result.value.filings
    assert len(filings) == 3
    assert filings[0].form_type == "10-K"
    assert filings[0].filing_date == date(2026, 1, 29)
    assert filings[1].form_type == "10-Q"
    assert filings[2].form_type == "10-Q"


def test_only_10k_10q_forms(msft_payload: dict[str, Any]) -> None:
    """8-K 被排除；默认只保留 10-K/10-Q。"""
    tool = SECSubmissionsTool(_mock_client(msft_payload))
    result = tool.execute(FetchSubmissionsRequest(cik="0000789019", as_of_date=date(2026, 12, 31)))

    assert isinstance(result, ToolSuccess)
    forms = [f.form_type for f in result.value.filings]
    assert "8-K" not in forms
    assert all(f in ("10-K", "10-Q") for f in forms)
    assert len(forms) == 4  # 5 条申报中 8-K 被排除
    assert forms[0] == "10-Q"  # 最新在前


def test_http_status_error_maps_to_failure(msft_payload: dict[str, Any]) -> None:
    """429 → RATE_LIMITED（可重试）。"""
    tool = SECSubmissionsTool(_mock_client(msft_payload, status=429))
    result = tool.execute(FetchSubmissionsRequest(cik="0000789019", as_of_date=date(2026, 1, 1)))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.RATE_LIMITED
    assert result.error.is_retryable is True
