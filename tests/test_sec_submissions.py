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
    FetchAnnualFilingsRequest,
    FetchSubmissionsRequest,
    SECSubmissionsTool,
    build_document_url,
    build_submissions_page_url,
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
    assert build_submissions_url("0000789019") == "https://data.sec.gov/submissions/CIK0000789019.json"
    assert (
        build_document_url("0000789019", "0000950170-25-000009", "msft-20250630.htm")
        == "https://www.sec.gov/Archives/edgar/data/789019/000095017025000009/msft-20250630.htm"
    )
    assert build_submissions_page_url("CIK0000019617-submissions-001.json") == (
        "https://data.sec.gov/submissions/CIK0000019617-submissions-001.json"
    )
    with pytest.raises(ValueError):
        build_submissions_page_url("https://evil.example/file.json")


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


def _columns(rows: list[tuple[str, str, str, str]]) -> dict[str, list[str]]:
    return {
        "accessionNumber": [row[0] for row in rows],
        "filingDate": [row[1] for row in rows],
        "form": [row[2] for row in rows],
        "primaryDocument": [row[3] for row in rows],
    }


def test_history_pages_are_fetched_until_requested_forms_found() -> None:
    """JPM 型大申报主体：recent 无截止日前目标表单，分页中找齐后立即停止。"""
    main = {
        "filings": {
            "recent": _columns(
                [("future-q", "2025-11-04", "10-Q", "future.htm")]
            ),
            "files": [
                {
                    "name": "CIK0000019617-submissions-001.json",
                    "filingFrom": "2025-07-21",
                    "filingTo": "2025-08-20",
                },
                {
                    "name": "CIK0000019617-submissions-002.json",
                    "filingFrom": "2025-06-01",
                    "filingTo": "2025-07-20",
                },
                {
                    "name": "CIK0000019617-submissions-003.json",
                    "filingFrom": "2025-02-01",
                    "filingTo": "2025-05-31",
                },
                {
                    "name": "CIK0000019617-submissions-004.json",
                    "filingFrom": "2024-01-01",
                    "filingTo": "2025-01-31",
                },
            ],
        }
    }
    pages = {
        "/submissions/CIK0000019617-submissions-001.json": _columns(
            [("q-accession", "2025-08-05", "10-Q", "jpm-q.htm")]
        ),
        "/submissions/CIK0000019617-submissions-002.json": _columns(
            [("eight-k", "2025-06-15", "8-K", "jpm-8k.htm")]
        ),
        "/submissions/CIK0000019617-submissions-003.json": _columns(
            [("k-accession", "2025-02-14", "10-K", "jpm-k.htm")]
        ),
        "/submissions/CIK0000019617-submissions-004.json": _columns(
            [("old-k", "2024-02-15", "10-K", "old.htm")]
        ),
    }
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        payload = (
            main
            if request.url.path.endswith("CIK0000019617.json")
            else pages[request.url.path]
        )
        return httpx.Response(200, json=payload, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = SECSubmissionsTool(client).execute(
        FetchSubmissionsRequest(cik="0000019617", as_of_date=date(2025, 10, 31))
    )

    assert isinstance(result, ToolSuccess)
    assert [filing.form_type for filing in result.value.filings] == ["10-Q", "10-K"]
    assert result.value.history_pages_fetched == 3
    assert not any(path.endswith("submissions-004.json") for path in requested_paths)


def test_history_page_limit_is_hard_bound() -> None:
    main = {
        "filings": {
            "recent": _columns([]),
            "files": [
                {
                    "name": f"CIK0000019617-submissions-{number:03d}.json",
                    "filingFrom": f"2025-0{4 - number}-01",
                    "filingTo": f"2025-0{4 - number}-28",
                }
                for number in (1, 2, 3)
            ],
        }
    }
    empty_page = _columns([("x", "2025-01-01", "8-K", "x.htm")])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = main if request.url.path.endswith("CIK0000019617.json") else empty_page
        return httpx.Response(200, json=payload, request=request)

    result = SECSubmissionsTool(
        httpx.Client(transport=httpx.MockTransport(handler)), max_history_pages=2
    ).execute(FetchSubmissionsRequest(cik="0000019617", as_of_date=date(2025, 10, 31)))

    assert isinstance(result, ToolSuccess)
    assert result.value.filings == []
    assert result.value.history_pages_fetched == 2
    assert calls == 3  # 主文件 + 最多 2 个分页


def _annual_columns(
    rows: list[tuple[str, str, str, str, str | None]],
) -> dict[str, list[str | None]]:
    return {
        "accessionNumber": [row[0] for row in rows],
        "filingDate": [row[1] for row in rows],
        "form": [row[2] for row in rows],
        "primaryDocument": [row[3] for row in rows],
        "reportDate": [row[4] for row in rows],
    }


def test_annual_discovery_fetches_history_until_two_report_periods_found() -> None:
    main = {
        "filings": {
            "recent": _annual_columns(
                [("fy-2025", "2026-01-29", "10-K", "fy-2025.htm", "2025-06-30")]
            ),
            "files": [
                {
                    "name": "CIK0000019617-submissions-001.json",
                    "filingFrom": "2025-01-01",
                    "filingTo": "2025-12-31",
                },
                {
                    "name": "CIK0000019617-submissions-002.json",
                    "filingFrom": "2024-01-01",
                    "filingTo": "2024-12-31",
                },
            ],
        }
    }
    pages = {
        "/submissions/CIK0000019617-submissions-001.json": _annual_columns(
            [("fy-2024", "2025-01-29", "10-K", "fy-2024.htm", "2024-06-30")]
        ),
        "/submissions/CIK0000019617-submissions-002.json": _annual_columns(
            [("too-old", "2024-01-29", "10-K", "old.htm", "2023-06-30")]
        ),
    }
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        payload = (
            main if request.url.path.endswith("CIK0000019617.json") else pages[request.url.path]
        )
        return httpx.Response(200, json=payload, request=request)

    tool = SECSubmissionsTool(httpx.Client(transport=httpx.MockTransport(handler)))
    result = tool.fetch_annual_filings(
        FetchAnnualFilingsRequest(cik="0000019617", as_of_date=date(2026, 1, 29))
    )

    assert isinstance(result, ToolSuccess)
    assert [filing.report_period for filing in result.value.filings] == [
        date(2025, 6, 30),
        date(2024, 6, 30),
    ]
    assert result.value.history_pages_fetched == 1
    assert not any(path.endswith("submissions-002.json") for path in requested_paths)


def test_annual_discovery_keeps_report_date_and_amendment_metadata(
    msft_payload: dict[str, Any],
) -> None:
    payload = json.loads(json.dumps(msft_payload))
    recent = payload["filings"]["recent"]
    recent["accessionNumber"].append("0000950170-26-000099")
    recent["filingDate"].append("2026-02-15")
    recent["form"].append("10-K/A")
    recent["primaryDocument"].append("msft-20250630a.htm")
    recent["reportDate"].append("2025-06-30")

    result = SECSubmissionsTool(_mock_client(payload)).fetch_annual_filings(
        FetchAnnualFilingsRequest(cik="0000789019", as_of_date=date(2026, 2, 15))
    )

    assert isinstance(result, ToolSuccess)
    amendment = next(filing for filing in result.value.filings if filing.form_type == "10-K/A")
    assert amendment.report_period == date(2025, 6, 30)
    assert amendment.is_amendment is True
