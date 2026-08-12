"""SECCompanyFactsTool（P02-06）：拉取并保真解析 XBRL Company Facts。

调用 data.sec.gov/api/xbrl/companyfacts/{CIK}.json，把每个 concept 在不同
unit/period 下的原始数值解析为 domain.FinancialFact（不计算、不改口径）。
测试用 httpx.MockTransport + fixture，不发起真实网络。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import FinancialFact
from invest_research.infrastructure.http.client import (
    classify_http_exception,
    classify_status_code,
)
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess

COMPANY_FACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"


class FetchFactsRequest(BaseModel):
    """请求：CIK + 目标分类法（默认 us-gaap）。"""

    model_config = {"frozen": True}

    cik: str
    taxonomy: str = "us-gaap"

    @field_validator("cik")
    @classmethod
    def _cik_10_digits(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) != 10:
            raise ValueError("cik 必须为 10 位数字")
        return v


class FetchFactsResponse(BaseModel):
    """输出：保真解析出的 FinancialFact 列表。"""

    model_config = {"frozen": True}

    facts: list[FinancialFact]


def build_company_facts_url(cik: str) -> str:
    """构造 Company Facts 端点 URL。"""
    return f"{COMPANY_FACTS_BASE}/CIK{cik}.json"


def _to_fact(
    company_id: str,
    taxonomy: str,
    concept: str,
    label: str | None,
    unit: str,
    entry: dict[str, Any],
) -> FinancialFact:
    """把单个 XBRL fact 条目映射为 FinancialFact（期间/时点二选一）。"""
    value = Decimal(str(entry["val"]))
    start = entry.get("start")
    end = entry.get("end")
    if start and end:
        return FinancialFact(
            company_id=company_id,
            source_id="",  # 占位：来源 id 由上层/后续任务填充
            taxonomy=taxonomy,
            concept=concept,
            label=label,
            value=value,
            unit=unit,
            period_start=date.fromisoformat(start),
            period_end=date.fromisoformat(end),
            form_type=entry.get("form"),
            fact_version="v1",
        )
    # 时点型（instant）：仅有 end（end 必须存在）
    if not end:
        raise ValueError("instant fact 缺少 end 日期")
    return FinancialFact(
        company_id=company_id,
        source_id="",
        taxonomy=taxonomy,
        concept=concept,
        label=label,
        value=value,
        unit=unit,
        instant_date=date.fromisoformat(end),
        form_type=entry.get("form"),
        fact_version="v1",
    )


def parse_company_facts(payload: dict[str, Any], cik: str, taxonomy: str) -> list[FinancialFact]:
    """从 Company Facts payload 提取指定 taxonomy 下所有 concept 的事实。"""
    facts_block = payload.get("facts", {}).get(taxonomy, {})
    if not isinstance(facts_block, dict):
        return []
    result: list[FinancialFact] = []
    for concept, meta in facts_block.items():
        if not isinstance(meta, dict):
            continue
        label = meta.get("label")
        units = meta.get("units", {})
        if not isinstance(units, dict):
            continue
        for unit, entries in units.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or "val" not in entry:
                    continue
                try:
                    result.append(_to_fact(cik, taxonomy, concept, label, unit, entry))
                except (KeyError, ValueError):
                    # 单条脏数据跳过，不影响整体解析
                    continue
    return result


class SECCompanyFactsTool:
    """SEC Company Facts 工具：注入 httpx.Client，解析 XBRL 事实（保真不做计算）。"""

    name = "sec_company_facts"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def execute(self, request: FetchFactsRequest) -> ToolResult[FetchFactsResponse]:
        try:
            response = self._client.get(build_company_facts_url(request.cik))
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
        facts = parse_company_facts(payload, request.cik, request.taxonomy)
        return ToolSuccess(value=FetchFactsResponse(facts=facts))
