"""SECSubmissionsTool（P02-05）：按截止日过滤、选取 10-K/10-Q。

调用 data.sec.gov/submissions/{CIK}.json，解析申报历史为 domain Filing。
测试用 httpx.MockTransport + fixture，不发起真实网络。
"""

from __future__ import annotations

import json
import re
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
DEFAULT_MAX_HISTORY_PAGES = 12
_PAGE_NAME_RE = re.compile(r"^CIK\d{10}-submissions-\d+\.json$")
_ANNUAL_FORMS: tuple[str, ...] = ("10-K", "10-K/A")


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
    history_pages_fetched: int = 0


class FetchAnnualFilingsRequest(BaseModel):
    """P07-02：只发现年度 10-K/10-K/A，保持 legacy 请求模型不变。"""

    model_config = {"frozen": True}

    cik: str
    as_of_date: date

    @field_validator("cik")
    @classmethod
    def _cik_10_digits(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.isdigit() or len(cleaned) != 10:
            raise ValueError("cik 必须为 10 位数字")
        return cleaned


def build_submissions_url(cik: str) -> str:
    """构造 submissions 端点 URL（SEC 要求 CIK 前缀，如 CIK0000320193.json）。"""
    return f"{SUBMISSIONS_BASE}/CIK{cik}.json"


def build_submissions_page_url(name: str) -> str:
    """Build a safe SEC-provided historical submissions page URL."""
    cleaned = name.strip()
    if not _PAGE_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"非法 SEC submissions 分页文件名: {name!r}")
    return f"{SUBMISSIONS_BASE}/{cleaned}"


def build_document_url(cik: str, accession: str, primary_document: str) -> str:
    """由申报元数据构造 primary document URL（SEC Archives）。"""
    cik_no_zeros = str(int(cik))
    accession_clean = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_clean}/{primary_document}"


def filter_filings(
    payload_filings: dict[str, Any], as_of_date: date, requested_forms: tuple[str, ...]
) -> list[dict[str, Any]]:
    """过滤主 recent 或历史分页：表单精确匹配 + filingDate<=as_of_date。

    表单精确等于（10-K/A 不算 10-K）。
    返回按 filingDate 从新到旧排序的原始 dict 列表。
    """
    # Main CIK response nests columns under filings.recent; historical page files
    # expose the same columnar fields directly at the top level.
    recent = payload_filings.get("recent", payload_filings)
    if not isinstance(recent, dict):
        return []
    accessions = recent.get("accessionNumber")
    filing_dates = recent.get("filingDate")
    forms = recent.get("form")
    primary_documents = recent.get("primaryDocument")
    report_dates = recent.get("reportDate")
    if not all(
        isinstance(column, list)
        for column in (accessions, filing_dates, forms, primary_documents)
    ):
        return []
    assert isinstance(accessions, list)
    assert isinstance(filing_dates, list)
    assert isinstance(forms, list)
    assert isinstance(primary_documents, list)
    if not isinstance(report_dates, list):
        report_dates = [None] * len(accessions)
    rows: list[tuple[date, dict[str, Any]]] = []
    for index, (acc, fdate, form, pdoc) in enumerate(
        zip(accessions, filing_dates, forms, primary_documents, strict=False)
    ):
        if not isinstance(form, str) or form not in requested_forms:
            continue
        if not isinstance(fdate, str):
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
                    "reportDate": report_dates[index] if index < len(report_dates) else None,
                },
            )
        )
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


def _history_page_descriptors(payload: dict[str, Any], as_of_date: date) -> list[dict[str, Any]]:
    """Return relevant SEC history pages newest-first without trusting their input order."""
    raw_files = payload.get("filings", {}).get("files", [])
    if not isinstance(raw_files, list):
        return []
    pages: list[tuple[date, dict[str, Any]]] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        filing_from = raw.get("filingFrom")
        filing_to = raw.get("filingTo")
        if not isinstance(name, str) or not isinstance(filing_from, str):
            continue
        try:
            start = date.fromisoformat(filing_from)
            end = date.fromisoformat(str(filing_to)) if filing_to else start
            build_submissions_page_url(name)
        except ValueError:
            continue
        if start > as_of_date:
            continue
        pages.append((end, raw))
    pages.sort(key=lambda item: item[0], reverse=True)
    return [raw for _, raw in pages]


def _deduplicate_and_sort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_accession: dict[str, dict[str, Any]] = {}
    for row in rows:
        accession = str(row.get("accessionNumber") or "")
        if accession:
            by_accession.setdefault(accession, row)
    return sorted(by_accession.values(), key=lambda row: str(row["filingDate"]), reverse=True)


def _report_period(raw_value: object) -> date | None:
    if not isinstance(raw_value, str) or not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def _build_filings(cik: str, rows: list[dict[str, Any]]) -> list[Filing]:
    return [
        Filing(
            accession_number=row["accessionNumber"],
            form_type=row["form"],
            filing_date=date.fromisoformat(row["filingDate"]),
            report_period=_report_period(row.get("reportDate")),
            primary_document_url=build_document_url(
                cik, row["accessionNumber"], row["primaryDocument"]
            ),
            is_amendment=row["form"].endswith("/A"),
        )
        for row in rows
    ]


def _annual_report_periods(rows: list[dict[str, Any]]) -> set[date]:
    return {
        report_period
        for row in rows
        if row.get("form") in _ANNUAL_FORMS
        for report_period in (_report_period(row.get("reportDate")),)
        if report_period is not None
    }


class SECSubmissionsTool:
    """SEC submissions 工具：注入 httpx.Client（依赖倒置），解析并过滤申报。"""

    name = "sec_submissions"

    def __init__(
        self,
        client: httpx.Client,
        *,
        max_history_pages: int = DEFAULT_MAX_HISTORY_PAGES,
    ) -> None:
        if max_history_pages < 0:
            raise ValueError("max_history_pages 必须 >= 0")
        self._client = client
        self._max_history_pages = max_history_pages

    def _get_json(self, url: str) -> tuple[dict[str, Any] | None, ToolFailure | None]:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            return None, ToolFailure(
                error=ToolError(
                    error_code=classify_http_exception(exc), message=f"SEC 请求失败: {exc}"
                )
            )
        if response.is_error:
            code = classify_status_code(response.status_code)
            return None, ToolFailure(
                error=ToolError(
                    error_code=code if code else ErrorCode.INTERNAL_BUG,
                    message=f"SEC HTTP {response.status_code}",
                )
            )
        try:
            payload = json.loads(response.text)
        except (TypeError, ValueError) as exc:
            return None, ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.INTERNAL_BUG,
                    message=f"SEC JSON 无法解析: {type(exc).__name__}",
                )
            )
        if not isinstance(payload, dict):
            return None, ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.INTERNAL_BUG,
                    message="SEC JSON 顶层必须是 object",
                )
            )
        return payload, None

    def execute(self, request: FetchSubmissionsRequest) -> ToolResult[FetchSubmissionsResponse]:
        payload, failure = self._get_json(build_submissions_url(request.cik))
        if failure is not None:
            return failure
        assert payload is not None
        raw_filings = filter_filings(
            payload.get("filings", {}), request.as_of_date, request.requested_forms
        )
        found_forms = {str(row["form"]) for row in raw_filings}
        requested_forms = set(request.requested_forms)
        history_pages_fetched = 0

        if not requested_forms.issubset(found_forms):
            for descriptor in _history_page_descriptors(payload, request.as_of_date):
                if history_pages_fetched >= self._max_history_pages:
                    break
                page_name = str(descriptor["name"])
                page, failure = self._get_json(build_submissions_page_url(page_name))
                if failure is not None:
                    return failure
                assert page is not None
                history_pages_fetched += 1
                page_rows = filter_filings(page, request.as_of_date, request.requested_forms)
                raw_filings.extend(page_rows)
                found_forms.update(str(row["form"]) for row in page_rows)
                if requested_forms.issubset(found_forms):
                    break

        raw_filings = _deduplicate_and_sort(raw_filings)

        filings = _build_filings(request.cik, raw_filings)
        return ToolSuccess(
            value=FetchSubmissionsResponse(
                filings=filings,
                history_pages_fetched=history_pages_fetched,
            )
        )

    def fetch_annual_filings(
        self, request: FetchAnnualFilingsRequest
    ) -> ToolResult[FetchSubmissionsResponse]:
        """获取两个不同 reportDate 年度所需的 annual filings（P07-02）。"""
        payload, failure = self._get_json(build_submissions_url(request.cik))
        if failure is not None:
            return failure
        assert payload is not None
        raw_filings = filter_filings(payload.get("filings", {}), request.as_of_date, _ANNUAL_FORMS)
        history_pages_fetched = 0

        for descriptor in _history_page_descriptors(payload, request.as_of_date):
            if len(_annual_report_periods(raw_filings)) >= 2:
                break
            if history_pages_fetched >= self._max_history_pages:
                break
            page, failure = self._get_json(build_submissions_page_url(str(descriptor["name"])))
            if failure is not None:
                return failure
            assert page is not None
            history_pages_fetched += 1
            raw_filings.extend(filter_filings(page, request.as_of_date, _ANNUAL_FORMS))

        raw_filings = _deduplicate_and_sort(raw_filings)
        return ToolSuccess(
            value=FetchSubmissionsResponse(
                filings=_build_filings(request.cik, raw_filings),
                history_pages_fetched=history_pages_fetched,
            )
        )
