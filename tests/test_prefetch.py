"""P05.5-3 并行预取与工具缓存测试（fake 工具，不联网）。"""

from __future__ import annotations

from datetime import date

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import CompanyIdentity, ResearchRequest
from invest_research.infrastructure.real_tools import (
    ResearchToolkit,
    build_research_tools,
    resolve_and_prefetch,
)
from invest_research.infrastructure.tool_cache import ToolCallCache
from invest_research.tools.base import ToolError, ToolFailure, ToolSuccess
from invest_research.tools.company_resolver import ResolveCompanyResponse
from invest_research.tools.google_search import SearchResponse
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


def _toolkit(resolver: object, submissions: object, search: object) -> ResearchToolkit:
    return ResearchToolkit(
        resolver=resolver,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        facts=None,  # type: ignore[arg-type]
        downloader=None,  # type: ignore[arg-type]
        search=search,  # type: ignore[arg-type]
    )


def test_resolve_and_prefetch_primes_cache_and_returns_identity() -> None:
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[_identity()]))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()

    identity = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert identity is not None
    assert identity.cik == _CIK
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


def test_prefetch_skips_fetch_on_cache_hit() -> None:
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[_identity()]))
    )
    submissions = _RaisingTool()
    search = _RaisingTool()
    cache = ToolCallCache()
    as_of = _AS_OF.isoformat()
    # 预热缓存：与 resolve_and_prefetch 使用相同键
    sub_key = cache.key(
        "sec_submissions",
        {"cik": _CIK, "as_of_date": as_of, "requested_forms": "10-K,10-Q"},
    )
    cache.put(sub_key, "cached-submissions")
    cache.put(cache.key("web_search", {"query": "MSFT", "as_of": as_of}), "cached-search")

    identity = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert identity is not None
    assert identity.cik == _CIK
    assert submissions.calls == 0  # 命中缓存，未执行真实获取
    assert search.calls == 0


def test_prefetch_returns_none_on_ambiguous_resolution() -> None:
    resolver = _CountingTool(
        ToolSuccess(value=ResolveCompanyResponse(resolved=False, candidates=[_identity()]))
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()

    identity = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert identity is None
    assert submissions.calls == 0  # 未解析则不预取
    assert search.calls == 0


def test_prefetch_returns_none_on_resolve_failure() -> None:
    resolver = _CountingTool(
        ToolFailure(
            error=ToolError(error_code=ErrorCode.INPUT_INVALID, message="未找到公司: MSFT")
        )
    )
    submissions = _CountingTool(ToolSuccess(value=FetchSubmissionsResponse(filings=[])))
    search = _CountingTool(ToolSuccess(value=SearchResponse(items=(), total=0, page=1)))
    cache = ToolCallCache()

    identity = resolve_and_prefetch(_request(), _toolkit(resolver, submissions, search), cache)

    assert identity is None
    assert submissions.calls == 0
    assert search.calls == 0


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
