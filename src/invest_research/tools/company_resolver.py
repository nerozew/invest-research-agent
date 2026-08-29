"""Deterministic local SEC company resolution (name/ticker/CIK -> identity).

Production requests read a versioned snapshot of the SEC's official company-ticker
dataset bundled with the application. Request execution never downloads or refreshes
that dataset. Exact ticker, exact CIK, normalized legal name, and a small curated alias
table are supported; fuzzy guessing is intentionally forbidden.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Iterable, Mapping

from pydantic import BaseModel, Field, field_validator

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import CompanyIdentity
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess

SNAPSHOT_RESOURCE = "sec_company_tickers_snapshot.json"
SNAPSHOT_SCHEMA_VERSION = 1

# Aliases never create identities: each target must exist in the official snapshot.
_CURATED_ALIASES: dict[str, str] = {
    "amazon": "AMZN",
    "apple": "AAPL",
    "boeing": "BA",
    "coca cola": "KO",
    "exxon": "XOM",
    "exxon mobil": "XOM",
    "johnson and johnson": "JNJ",
    "johnson johnson": "JNJ",
    "jp morgan": "JPM",
    "jp morgan chase": "JPM",
    "jpmorgan": "JPM",
    "jpmorgan chase": "JPM",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "walmart": "WMT",
}


class ResolveCompanyRequest(BaseModel):
    """Company resolver input: non-empty company name, ticker, or CIK."""

    model_config = {"frozen": True}
    input_company: str = Field(min_length=1, max_length=200)

    @field_validator("input_company")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("input_company 不能为空或纯空白")
        return cleaned


class ResolveCompanyResponse(BaseModel):
    """Unique match or ambiguity candidates; empty matches are ToolFailure."""

    model_config = {"frozen": True}
    resolved: bool
    candidates: list[CompanyIdentity]


def _normalize_lookup_key(value: str) -> str:
    """Normalize names without performing fuzzy or prefix matching."""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _ticker_keys(ticker: str) -> set[str]:
    value = ticker.strip().upper()
    return {
        value.casefold(),
        value.replace(".", "-").casefold(),
        value.replace("-", ".").casefold(),
    }


class CompanyIndex:
    """Exact in-memory index with deterministic ambiguity handling.

    ``entries`` remains injectable for isolated tests. Snapshot identities are indexed
    by ticker, 10-digit CIK, and normalized legal name. Multiple ticker classes sharing
    one CIK are one issuer rather than an ambiguity.
    """

    def __init__(
        self,
        entries: Iterable[tuple[str, CompanyIdentity]],
        *,
        aliases: Mapping[str, str] | None = None,
        source_url: str | None = None,
        retrieved_at: str | None = None,
        company_count: int | None = None,
    ) -> None:
        self._index: dict[str, list[CompanyIdentity]] = {}
        self.source_url = source_url
        self.retrieved_at = retrieved_at
        self.company_count = company_count
        identities: dict[tuple[str, str | None], CompanyIdentity] = {}

        for key, identity in entries:
            identities[(identity.cik, identity.ticker)] = identity
            self._add(key, identity)
            self._add(identity.cik, identity)
            self._add(identity.legal_name, identity)
            if identity.ticker:
                for ticker_key in _ticker_keys(identity.ticker):
                    self._add(ticker_key, identity)

        by_ticker: dict[str, CompanyIdentity] = {}
        for identity in identities.values():
            if identity.ticker:
                for ticker_key in _ticker_keys(identity.ticker):
                    by_ticker[ticker_key] = identity
        for alias, ticker in (aliases or {}).items():
            target = by_ticker.get(ticker.casefold())
            if target is None:
                raise ValueError(f"公司别名 {alias!r} 指向快照中不存在的 ticker {ticker!r}")
            self._add(alias, target)

    def _add(self, key: str, identity: CompanyIdentity) -> None:
        normalized = self._key(key)
        if not normalized:
            return
        candidates = self._index.setdefault(normalized, [])
        if identity not in candidates:
            candidates.append(identity)

    @staticmethod
    def _key(query: str) -> str:
        stripped = query.strip()
        if stripped.isdigit() and len(stripped) <= 10:
            return stripped.zfill(10)
        return _normalize_lookup_key(stripped)

    def lookup(self, query: str) -> list[CompanyIdentity]:
        """Return exact candidates and deduplicate share classes by issuer CIK."""
        candidates = self._index.get(self._key(query), [])
        by_cik: dict[str, CompanyIdentity] = {}
        for identity in candidates:
            by_cik.setdefault(identity.cik, identity)
        return list(by_cik.values())


def _snapshot_payload() -> dict[str, object]:
    resource = resources.files("invest_research.resources").joinpath(SNAPSHOT_RESOURCE)
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("SEC 公司索引快照顶层格式无效")
    return payload


@lru_cache(maxsize=1)
def load_sec_company_index() -> CompanyIndex:
    """Load and validate the bundled SEC snapshot once per process."""
    payload = _snapshot_payload()
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError("SEC 公司索引快照 schema_version 不受支持")
    raw_companies = payload.get("companies")
    if not isinstance(raw_companies, list):
        raise RuntimeError("SEC 公司索引快照缺少 companies 数组")
    declared_count = payload.get("company_count")
    if not isinstance(declared_count, int) or declared_count != len(raw_companies):
        raise RuntimeError("SEC 公司索引快照 company_count 与实际条目数不一致")
    if declared_count < 5_000:
        raise RuntimeError("SEC 公司索引快照异常偏小，拒绝加载")

    entries: list[tuple[str, CompanyIdentity]] = []
    for item in raw_companies:
        if not isinstance(item, dict):
            raise RuntimeError("SEC 公司索引快照含无效公司条目")
        try:
            identity = CompanyIdentity(
                cik=str(item["cik"]),
                ticker=str(item["ticker"]),
                legal_name=str(item["legal_name"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("SEC 公司索引快照含无法解析的公司条目") from exc
        entries.append((identity.ticker or "", identity))

    return CompanyIndex(
        entries,
        aliases=_CURATED_ALIASES,
        source_url=str(payload.get("source_url") or ""),
        retrieved_at=str(payload.get("retrieved_at") or ""),
        company_count=declared_count,
    )


class CompanyResolverTool:
    """Resolve locally; ambiguity is explicit and absence fails closed."""

    name = "company_resolver"

    def __init__(self, index: CompanyIndex | None = None) -> None:
        self._index = index if index is not None else load_sec_company_index()

    def execute(self, request: ResolveCompanyRequest) -> ToolResult[ResolveCompanyResponse]:
        candidates = self._index.lookup(request.input_company)
        if not candidates:
            snapshot_hint = (
                f"（本地 SEC 快照 retrieved_at={self._index.retrieved_at}）"
                if self._index.retrieved_at
                else ""
            )
            return ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.INPUT_INVALID,
                    message=(
                        f"本地 SEC 公司索引未找到精确匹配: {request.input_company}"
                        f"{snapshot_hint}；未执行模糊猜测"
                    ),
                )
            )
        if len(candidates) == 1:
            return ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=candidates))
        return ToolSuccess(value=ResolveCompanyResponse(resolved=False, candidates=candidates))
