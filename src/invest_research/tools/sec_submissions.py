"""SECSubmissionsTool（P02-05）：按截止日过滤、选取 10-K/10-Q。

调用 data.sec.gov/submissions/{CIK}.json，解析申报历史为 domain Filing。
测试用 httpx.MockTransport + fixture，不发起真实网络。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import Filing
from invest_research.infrastructure.http.client import (
    classify_http_exception,
    classify_status_code,
)
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess

SUBMISSIONS_BASE = "https://data.sec.gov/submissions"


class FetchSubmissionsRequest(BaseModel):
    """请求：10 位 CIK + 数据截止日 + 目标表单。"""

    model_config = {"frozen": True}

    cik: str
    as_of_date: date
    requested_forms: tuple[str, ...] = ("10-K", "10-Q")

    @field_validator("cik")
    @classmethod
    def _cik_10_digits(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) != 10:
            raise ValueError("cik 必须为 10 位数字")
        return v


class FetchSubmissionsResponse(BaseModel):
    """输出：符合格式与截止日的 Filing 列表（按申报日期降序）。"""

    model_config = {"frozen": True}

    filings: list[Filing]


def build_submissions_url(cik: str) -> str:
    """构造 submissions 端点 URL（SEC 要求 CIK 前缀，如 CIK0000320193.json）。"""
    return f"{SUBMISSIONS_BASE}/CIK{cik}.json"


def build_document_url(cik: str, accession: str, primary_document: str) -> str:
    """由申报元数据构造 primary document URL（SEC Archives）。"""
    cik_no_zeros = str(int(cik))
    accession_clean = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_clean}/{primary_document}"


def filter_filings(
    payload_filings: dict[str, Any], as_of_date: date, requested_forms: tuple[str, ...]
) -> list[dict[str, Any]]:
    """从 submissions 的 recent 段过滤：表单精确匹配 + filingDate<=as_of_date。

    只取 recent（MVP 足够）；表单精确等于（10-K/A 不算 10-K）。
    返回按 filingDate 从新到旧排序的原始 dict 列表。
    """
    recent = payload_filings.get("recent", {})
    if not isinstance(recent, dict):
        return []
    if not isinstance(recent.get("accessionNumber"), list):
        return []
    rows: list[tuple[date, dict[str, Any]]] = []
    for acc, fdate, form, pdoc in zip(
        recent["accessionNumber"],
        recent["filingDate"],
        recent["form"],
        recent["primaryDocument"],
    ):
        if form not in requested_forms:
            continue
        filing_date = date.fromisoformat(fdate)
        if filing_date > as_of_date:
            continue
        rows.append(
            (
                filing_date,
                {
                    "accessionNumber": acc,
                    "filingDate": fdate,
                    "form": form,
                    "primaryDocument": pdoc,
                },
            )
        )
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


class SECSubmissionsTool:
    """SEC submissions 工具：注入 httpx.Client（依赖倒置），解析并过滤申报。"""

    name = "sec_submissions"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def execute(self, request: FetchSubmissionsRequest) -> ToolResult[FetchSubmissionsResponse]:
        try:
            response = self._client.get(build_submissions_url(request.cik))
        except httpx.HTTPError as exc:
            return ToolFailure(
                error=ToolError(
                    error_code=classify_http_exception(exc), message=f"SEC 请求失败: {exc}"
                )
            )

        if response.is_error:
            code = classify_status_code(response.status_code)
            return ToolFailure(
                error=ToolError(
                    error_code=code if code else ErrorCode.INTERNAL_BUG,
                    message=f"SEC HTTP {response.status_code}",
                )
            )

        payload = json.loads(response.text)
        raw_filings = filter_filings(
            payload.get("filings", {}), request.as_of_date, request.requested_forms
        )

        filings = [
            Filing(
                accession_number=row["accessionNumber"],
                form_type=row["form"],
                filing_date=date.fromisoformat(row["filingDate"]),
                primary_document_url=build_document_url(
                    request.cik, row["accessionNumber"], row["primaryDocument"]
                ),
            )
            for row in raw_filings
        ]
        return ToolSuccess(value=FetchSubmissionsResponse(filings=filings))
