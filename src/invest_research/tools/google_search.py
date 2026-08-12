"""P02-17 GoogleSearchTool provider interface（anti-corruption layer）。

职责：
- ``SearchProvider``：搜索提供商抽象接口（业务代码只依赖此接口，不依赖任何
  具体提供商响应结构——对齐 docs/02 §4「供应商必须封装在接口后面」）；
- ``SearchQuery`` / ``SearchResult`` / ``SearchResponse``：统一 Pydantic 契约；
- ``GoogleSearchTool``：P02-01 契约工具，注入 provider；结果按 canonical URL
  去重（复用 P02-07），provider 异常归一为 ToolFailure。

设计：
- ``SearchProvider`` 用 ``metaclass=abc.ABCMeta`` + 自定义 ``__subclasshook__``：
  结构上具备 ``search(query) -> SearchResponse`` 的实现即视为 provider
  （鸭子类型，fake 零绑定，``isinstance(provider, SearchProvider)`` 通过）；
  抽象方法 ``search`` 保证 ``SearchProvider()`` 直接实例化抛 TypeError。
- 分页语义：``page >= 1``，``page_size >= 1``；as_of 过滤由 provider 负责
  （各提供商对"发布时间"表示不同，接口收口在 provider 内最合理）。

依赖边界：只依赖标准库、Pydantic、domain 层、tools/base 与 tools/urls；
禁止导入 CrewAI/FastAPI/SQLAlchemy。外部服务在测试中用 fake provider。
"""

from __future__ import annotations

import abc
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from invest_research.domain.errors import ErrorCode
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess
from invest_research.tools.urls import canonicalize_url


class SearchProvider(metaclass=abc.ABCMeta):
    """搜索提供商抽象接口（结构性匹配：具备 search 方法即可）。"""

    @classmethod
    def __subclasshook__(cls, subclass: type) -> bool:
        if cls is SearchProvider:
            if any("search" in vars(base) for base in subclass.__mro__):
                return True
        # NotImplemented 让 ABCMeta 继续默认判 <：mypy 对 NotImplemented 的类型
        # 推断为 Any，此处按需豁免（与 P02-10 type: ignore[import-untyped] 同思路）
        return NotImplemented  # type: ignore[no-any-return]

    @abc.abstractmethod
    def search(self, query: "SearchQuery") -> "SearchResponse":
        """按 query 执行搜索并返回统一响应（as_of/分页由 provider 完成）。"""
        raise NotImplementedError


class SearchQuery(BaseModel):
    """搜索请求：关键字 + as_of + 分页。"""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=200)
    as_of: date = Field(default_factory=date.today)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=50)


class SearchResult(BaseModel):
    """单条搜索结果（规范化：标题/URL/发布者/发布时间/访问时间）。"""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    publisher: str | None = None
    published_at: date
    accessed_at: datetime


class SearchResponse(BaseModel):
    """搜索结果响应：items + total + 当前页。"""

    model_config = ConfigDict(frozen=True)

    items: tuple[SearchResult, ...] = ()
    total: int = Field(ge=0)
    page: int = Field(ge=1)


class GoogleSearchTool:
    """P02-01 契约工具：注入 provider，返回去重后的搜索结果。

    去重：按 P02-07 canonical URL 保留首个（去除 tracking 参数等变体）；
    无效 URL 被剔除。provider 抛出的异常归一为 ToolFailure(INTERNAL_BUG)。
    """

    name = "google_search"

    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    def execute(self, request: SearchQuery) -> ToolResult[SearchResponse]:
        try:
            response = self._provider.search(request)
        except Exception as exc:  # 应用边界：记录并转换为统一失败，不静默吞错
            return ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.INTERNAL_BUG,
                    message=f"搜索服务异常: {type(exc).__name__}: {exc}",
                    details={"provider": type(self._provider).__name__},
                )
            )

        # 按 canonical URL 去重（复用 P02-07 canonicalize_url），无效 URL 剔除；
        # 去重后的结果 URL 统一替换为 canonical 形式（对齐 sources.canonical_url）
        by_canonical: dict[str, SearchResult] = {}
        for r in response.items:
            canon = canonicalize_url(r.url)
            if canon is not None and canon not in by_canonical:
                by_canonical[canon] = r.model_copy(update={"url": canon})
        deduped = tuple(by_canonical.values())

        return ToolSuccess(
            value=SearchResponse(
                items=deduped,
                total=response.total,
                page=response.page,
            )
        )
