"""P02-17 GoogleSearchTool provider interface 契约测试。

验证目标（docs/05 P02-17 验收）：
- provider interface：SearchProvider 抽象（abc.ABC，业务代码只依赖接口）；
- 查询契约：SearchQuery（query + as_of + page + page_size）→ SearchResponse；
- as-of：结果按 published_at 过滤，不返回晚于 as_of 的结果；
- 分页：page/page_size 语义正确；
- 去重：工具层用 canonical URL 去重（复用 P02-07 deduplicate_sources）；
- fake provider：不联网，覆盖上述契约；
- 满足 P02-01 Tool 契约（name + execute → ToolResult）。

不修改数据库、不联网、不依赖外部搜索服务。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

import pytest
from pydantic import ValidationError

from invest_research.domain.errors import ErrorCode
from invest_research.tools.base import Tool, ToolFailure, ToolSuccess
from invest_research.tools.google_search import (
    GoogleSearchTool,
    SearchProvider,
    SearchQuery,
    SearchResponse,
    SearchResult,
)


@runtime_checkable
class _ShapeSearchProvider(Protocol):
    """结构化 provider：仅凭成员形状满足契约（鸭子类型）。"""

    def search(self, query: SearchQuery) -> SearchResponse: ...


class FakeSearchProvider:
    """fake provider：内存结果，按 as_of/published_at/page/page_size 过滤分页。"""

    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    def search(self, query: SearchQuery) -> SearchResponse:
        filtered = [r for r in self._results if r.published_at <= query.as_of]
        start = (query.page - 1) * query.page_size
        page = filtered[start : start + query.page_size]
        return SearchResponse(items=tuple(page), total=len(filtered), page=query.page)


def _result(url: str, published: date, title: str = "t") -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        publisher="example.com",
        published_at=published,
        accessed_at=datetime(2025, 1, 1, 0, 0, 0),
    )


# ---------------------------------------------------------------------------
# SearchQuery 模型校验
# ---------------------------------------------------------------------------


def test_query_requires_nonempty_query() -> None:
    """query 为空 → ValidationError。"""
    with pytest.raises(ValidationError):
        SearchQuery(query="")


def test_query_page_must_be_positive() -> None:
    """page 必须 >= 1。"""
    with pytest.raises(ValidationError):
        SearchQuery(query="msft", page=0)


# ---------------------------------------------------------------------------
# SearchProvider 契约
# ---------------------------------------------------------------------------


def test_fake_provider_satisfies_protocol() -> None:
    """fake provider 结构上满足 SearchProvider 契约。"""
    provider = FakeSearchProvider([])
    assert isinstance(provider, _ShapeSearchProvider)
    assert isinstance(provider, SearchProvider)  # runtime_checkable ABC


def test_search_provider_abc_abstract() -> None:
    """SearchProvider 是抽象接口，不能直接实例化。"""
    with pytest.raises(TypeError):
        SearchProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# GoogleSearchTool 契约（P02-01 Tool）
# ---------------------------------------------------------------------------


def test_tool_satisfies_tool_contract() -> None:
    """GoogleSearchTool 满足 P02-01 Tool 契约。"""
    tool = GoogleSearchTool(FakeSearchProvider([]))
    assert tool.name == "google_search"
    assert isinstance(tool, Tool)


def test_tool_search_returns_success() -> None:
    """查询成功：ToolSuccess 携带 SearchResponse。"""
    provider = FakeSearchProvider([_result("https://a.com/1", date(2025, 6, 1), title="A")])
    tool = GoogleSearchTool(provider)

    result = tool.execute(SearchQuery(query="msft", as_of=date(2025, 12, 31)))

    assert isinstance(result, ToolSuccess)
    assert result.value.total == 1
    assert result.value.items[0].url == "https://a.com/1"


# ---------------------------------------------------------------------------
# as-of 过滤
# ---------------------------------------------------------------------------


def test_as_of_filters_results() -> None:
    """as_of 只保留 published_at <= as_of 的结果。"""
    provider = FakeSearchProvider(
        [
            _result("https://a.com/old", date(2025, 1, 1), title="old"),
            _result("https://a.com/new", date(2025, 6, 1), title="new"),
        ]
    )
    tool = GoogleSearchTool(provider)

    result = tool.execute(SearchQuery(query="msft", as_of=date(2025, 3, 1)))

    assert isinstance(result, ToolSuccess)
    assert [r.url for r in result.value.items] == ["https://a.com/old"]


# ---------------------------------------------------------------------------
# 分页
# ---------------------------------------------------------------------------


def test_pagination_page_and_page_size() -> None:
    """分页：page=2 page_size=1 返回第二条。"""
    provider = FakeSearchProvider(
        [
            _result("https://a.com/1", date(2025, 6, 1), title="1"),
            _result("https://a.com/2", date(2025, 6, 2), title="2"),
        ]
    )
    tool = GoogleSearchTool(provider)

    result = tool.execute(SearchQuery(query="msft", as_of=date(2025, 12, 31), page=2, page_size=1))

    assert isinstance(result, ToolSuccess)
    assert [r.url for r in result.value.items] == ["https://a.com/2"]
    assert result.value.total == 2


# ---------------------------------------------------------------------------
# 去重（复用 P02-07 canonical URL）
# ---------------------------------------------------------------------------


def test_tool_deduplicates_by_canonical_url() -> None:
    """工具层用 canonical URL 去重（同页不同 utm 只保留首个）。"""
    provider = FakeSearchProvider(
        [
            _result("https://a.com/x?utm_source=1", date(2025, 6, 1), title="1"),
            _result("https://a.com/x?utm_medium=m", date(2025, 6, 1), title="2"),
            _result("https://a.com/y", date(2025, 6, 2), title="3"),
        ]
    )
    tool = GoogleSearchTool(provider)

    result = tool.execute(SearchQuery(query="msft", as_of=date(2025, 12, 31)))

    assert isinstance(result, ToolSuccess)
    urls = [r.url for r in result.value.items]
    assert urls == ["https://a.com/x", "https://a.com/y"]


# ---------------------------------------------------------------------------
# provider 异常 → ToolFailure
# ---------------------------------------------------------------------------


class FailingProvider:
    """抛出异常的 provider：应被 GoogleSearchTool 捕获为 ToolFailure。"""

    def search(self, query: SearchQuery) -> SearchResponse:
        raise RuntimeError("provider boom")


def test_provider_exception_maps_to_failure() -> None:
    """provider 异常 → ToolFailure（INTERNAL_BUG，不崩溃）。"""
    tool = GoogleSearchTool(FailingProvider())

    result = tool.execute(SearchQuery(query="msft", as_of=date(2025, 12, 31)))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INTERNAL_BUG
