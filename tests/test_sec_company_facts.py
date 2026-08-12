"""P02-06 SECCompanyFactsTool 测试（recorded fixture + MockTransport，不发起真实网络）。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from invest_research.domain.errors import ErrorCode
from invest_research.tools.base import ToolFailure, ToolSuccess
from invest_research.tools.sec_company_facts import (
    FetchFactsRequest,
    SECCompanyFactsTool,
    build_company_facts_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "companyfacts_msft.json"


def _mock_client(payload: dict, status: int = 200) -> httpx.Client:
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def msft_facts() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_build_url() -> None:
    """端点 URL 构造：Company Facts 接口用 CIK{10位}.json。"""
    assert (
        build_company_facts_url("0000789019")
        == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"
    )


def test_parses_facts_with_fidelity(msft_facts: dict) -> None:
    """保真解析：concept/period/unit/form 均保留，不做计算。"""
    tool = SECCompanyFactsTool(_mock_client(msft_facts))
    result = tool.execute(FetchFactsRequest(cik="0000789019"))

    assert isinstance(result, ToolSuccess)
    facts = result.value.facts
    # 期间型 4 条（Revenue×3 + EPS×1）+ 时点型 1 条（Assets）= 5
    assert len(facts) == 5

    revenue_fy = [
        f for f in facts if f.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    ][0]
    assert revenue_fy.taxonomy == "us-gaap"
    assert revenue_fy.period_start is not None
    assert revenue_fy.period_end.year == 2024
    assert revenue_fy.value == Decimal("245100000000.00")
    assert revenue_fy.unit == "USD"
    assert revenue_fy.form_type == "10-K"
    assert revenue_fy.fact_version == "v1"


def test_instant_fact_uses_instant_date(msft_facts: dict) -> None:
    """时点型（Assets）→ instant_date 非空、period_start/end 为空。"""
    tool = SECCompanyFactsTool(_mock_client(msft_facts))
    result = tool.execute(FetchFactsRequest(cik="0000789019"))

    assert isinstance(result, ToolSuccess)
    assets = [f for f in result.value.facts if f.concept == "Assets"]
    assert len(assets) == 1
    assert assets[0].instant_date is not None
    assert assets[0].period_start is None and assets[0].period_end is None
    assert assets[0].value == Decimal("512600000000.00")


def test_unit_preserved_for_ratios(msft_facts: dict) -> None:
    """单位保真：EPS 使用 USD/shares。"""
    tool = SECCompanyFactsTool(_mock_client(msft_facts))
    result = tool.execute(FetchFactsRequest(cik="0000789019"))

    assert isinstance(result, ToolSuccess)
    eps = [f for f in result.value.facts if f.concept == "EarningsPerShareBasic"]
    assert len(eps) == 1
    assert eps[0].unit == "USD/shares"
    assert eps[0].value == Decimal("11.8")


def test_http_status_error_maps_to_failure(msft_facts: dict) -> None:
    """429 → RATE_LIMITED（可重试）。"""
    tool = SECCompanyFactsTool(_mock_client(msft_facts, status=429))
    result = tool.execute(FetchFactsRequest(cik="0000789019"))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.RATE_LIMITED
    assert result.error.is_retryable is True
