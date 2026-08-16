"""P05.5-3 并行预取与工具缓存测试（fake 工具，不联网）。

P05.5-fix：resolve_and_prefetch 返回 PrefetchResult（公司身份 + SEC/Serper 摘要
+ 状态），供 runner 注入 Research Task。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import CompanyIdentity, FinancialFact, ResearchRequest
from invest_research.infrastructure.real_tools import (
    ResearchToolkit,
    build_research_tools,
    resolve_and_prefetch,
)
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache
from invest_research.tools.base import ToolError, ToolFailure, ToolSuccess
from invest_research.tools.company_resolver import ResolveCompanyResponse
from invest_research.tools.google_search import SearchResponse
from invest_research.tools.sec_company_facts import FetchFactsResponse
from invest_research.tools.sec_submissions import FetchSubmissionsResponse

_CIK = "0000789019"
_AS_OF = date(2025, 12, 31)


class _CountingTool:
    """返回预置 ToolResult 并记录调用次数的 fake 工具。"""

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls = 0

    def execute(self, request: object) -> object:  # noqa: ARG002 - fake 忽略入参
        self.calls += 1
        return self._result


class _RaisingTool:
    """一旦被调用就抛错的 fake 工具（用于断言缓存命中跳过执行）。"""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request: object) -> object:
        self.calls += 1
        raise RuntimeError("不应被调用（应命中缓存）")


def _identity() -> CompanyIdentity:
    return CompanyIdentity(cik=_CIK, ticker="MSFT", legal_name="MICROSOFT CORP")


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=_AS_OF)


def _facts_result() -> object:
    return ToolSuccess(
        value=FetchFactsResponse(
            facts=[
                FinancialFact(
                    company_id=_CIK,
                    source_id="",
                    taxonomy="us-gaap",
                    concept="RevenueFromContractWithCustomerExcludingAssessedTax",
                    value=Decimal("100"),
                    unit="USD",
                    period_start=date(2024, 1, 1),
                    period_end=date(2024, 12, 31),
                    form_type="10-K",
                    accession_number="0000789019-25-000001",
                )
            ]
        )
    )


def _toolkit(
    resolver: object,
    submissions: object,
    search: object,
    facts: object | None = None,
) -> ResearchToolkit:
    return ResearchToolkit(
        resolver=resolver,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        facts=facts if facts is not None else _CountingTool(_facts_result()),  # type: ignore[arg-type]
        downloader=None,  # type: ignore[arg-type]
        search=search,  # type: ignore[arg-type]
    )


def test_resolve_and_prefetch_primes_cache_and_returns_result() -> None:
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[_identity()]))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()

    result = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert result.company_identity is not None
    assert result.company_identity.cik == _CIK
    assert result.status == "ok"
    # 摘要必须随 PrefetchResult 返回（不只预热缓存）
    assert result.submissions_summary is not None
    assert result.search_summary is not None
    assert result.financial_facts_summary is not None
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in (result.financial_facts_summary)
    assert resolver.calls == 1
    assert submissions.calls == 1
    assert search.calls == 1
    # 缓存已预热：sec_submissions 与 web_search 均有命中
    as_of = _AS_OF.isoformat()
    sub_key = cache.key(
        "sec_submissions",
        {"cik": _CIK, "as_of_date": as_of, "requested_forms": "10-K,10-Q"},
    )
    assert cache.get(sub_key) is not None
    assert cache.get(cache.key("web_search", {"query": "MSFT", "as_of": as_of})) is not None
    assert cache.get(cache.key("sec_company_facts", {"cik": _CIK, "as_of_date": as_of})) is not None


def test_prefetch_skips_fetch_on_cache_hit() -> None:
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[_identity()]))
    )
    submissions = _RaisingTool()
    search = _RaisingTool()
    cache = ToolCallCache()
    as_of = _AS_OF.isoformat()
    sub_key = cache.key(
        "sec_submissions",
        {"cik": _CIK, "as_of_date": as_of, "requested_forms": "10-K,10-Q"},
    )
    cache.put(sub_key, "cached-submissions")
    cache.put(cache.key("web_search", {"query": "MSFT", "as_of": as_of}), "cached-search")
    cache.put(
        cache.key("sec_company_facts", {"cik": _CIK, "as_of_date": as_of}),
        "cached-facts",
    )

    result = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert result.company_identity is not None
    assert result.company_identity.cik == _CIK
    assert submissions.calls == 0  # 命中缓存，未执行真实获取
    assert search.calls == 0
    assert result.financial_facts_summary == "cached-facts"


def test_prefetch_failed_status_on_ambiguous_resolution() -> None:
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=False, candidates=[_identity()]))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()

    result = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert result.company_identity is None
    assert result.status == "failed"
    assert submissions.calls == 0  # 未解析则不预取
    assert search.calls == 0


def test_prefetch_failed_status_on_resolve_failure() -> None:
    resolver = _CountingTool(
        ToolFailure(error=ToolError(error_code=ErrorCode.INPUT_INVALID, message="未找到公司: MSFT"))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()

    result = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert result.company_identity is None
    assert result.status == "failed"
    assert submissions.calls == 0
    assert search.calls == 0


def test_prefetch_respects_tool_budget() -> None:
    """预取执行计入每 Job 工具硬预算（与 Agent 调用共用同一 ToolBudget）。"""
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[_identity()]))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()
    budget = ToolBudget(caps={"sec_submissions": 1, "sec_company_facts": 1, "web_search": 1})

    # 第一次预取：消耗 1 次 sec_submissions + 1 次 web_search
    result1 = resolve_and_prefetch(
        _request(), _toolkit(resolver, submissions, search), cache, budget=budget
    )
    assert result1.company_identity is not None
    assert result1.status == "ok"

    # 第二次预取：预算耗尽（缓存已有结果会命中；但用不同缓存验证预算）
    cache2 = ToolCallCache()
    result2 = resolve_and_prefetch(
        _request(), _toolkit(resolver, submissions, search), cache2, budget=budget
    )
    assert result2.company_identity is not None
    # 预算耗尽：不再执行真实获取（摘要为空 → partial）
    assert submissions.calls == 1
    assert search.calls == 1
    assert result2.status == "partial"


def test_build_research_tools_cache_executes_once() -> None:
    """相同工具名+规范化参数在单 Job 内只执行一次（缓存生效）。"""
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    toolkit = ResearchToolkit(
        resolver=None,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        facts=None,  # type: ignore[arg-type]
        downloader=None,  # type: ignore[arg-type]
        search=None,  # type: ignore[arg-type]
    )
    cache = ToolCallCache()
    tools = build_research_tools(toolkit=toolkit, cache=cache)
    sec_tool = tools[1]  # SECSubmissions

    kwargs = {"cik": _CIK, "as_of_date": _AS_OF.isoformat(), "requested_forms": "10-K,10-Q"}
    sec_tool.run(**kwargs)
    sec_tool.run(**kwargs)

    assert submissions.calls == 1


def test_prefetch_counts_into_invocation_stats() -> None:
    """预取的真实 SEC/Serper 调用必须计入 evidence stats（P05-13 验收 requirement7）。"""
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[_identity()]))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()
    stats: dict[str, int] = {}

    result = resolve_and_prefetch(
        _request(), _toolkit(resolver, submissions, search), cache, stats=stats
    )

    assert result.company_identity is not None
    assert stats.get("sec_submissions_calls") == 1
    assert stats.get("sec_company_facts_calls") == 1
    assert stats.get("web_search_calls") == 1
