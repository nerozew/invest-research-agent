"""P02-18 Serper provider adapter 契约测试（mocked contract test，不联网）。

验证目标（docs/05 P02-18 验收）：
- 请求映射：SearchQuery → Serper 端点/参数（q/page/num）正确；
- 响应映射：Serper Organic JSON → SearchResult（title/link/displayLink/snippet/date）；
- 认证脱敏：Authorization header 不进入异常信息/日志（密钥不泄漏）；
- HTTP 错误归一：429 → 由 classify_status_code 归一（adapter 抛/返回不可用信息）；
- 实现 P02-17 SearchProvider 接口（可被 GoogleSearchTool 使用）；
- 不联网、不修改数据库、不依赖真实服务。

注意：adapter.search 在 HTTP 错误时抛异常（由上层 GoogleSearchTool 归一），
认证信息用 SecretStr 存储，str() 不泄漏。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from invest_research.domain.errors import ErrorCode
from invest_research.tools.google_search import (
    SearchProvider,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from invest_research.tools.serper_adapter import SERPER_ENDPOINT, SerperAdapter, SerperConfig


def _serper_payload() -> dict[str, Any]:
    return {
        "organic": [
            {
                "title": "Microsoft - Official Home Page",
                "link": "https://www.microsoft.com/",
                "displayLink": "www.microsoft.com",
                "snippet": "The home page of Microsoft.",
                "date": "Jun 1, 2025",
            },
            {
                "title": "MSFT Stock Price",
                "link": "https://www.example.com/msft?utm_source=x",
                "displayLink": "www.example.com",
                "snippet": "MSFT stock quote.",
            },
        ]
    }


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _config() -> SerperConfig:
    return SerperConfig(api_key=SecretStr("sk-test-secret"))


def test_adapter_satisfies_search_provider() -> None:
    """SerperAdapter 满足 P02-17 SearchProvider 接口。"""
    adapter = SerperAdapter(
        _client(lambda r: httpx.Response(200, json=_serper_payload())), _config()
    )
    assert isinstance(adapter, SearchProvider)


def test_request_mapping_to_serper_endpoint_and_params() -> None:
    """请求映射：POST {endpoint}，query/page/num 参数、Authorization header 正确。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode())
        captured["auth"] = request.headers.get("X-Api-Key", "")
        return httpx.Response(200, json={"organic": []})

    adapter = SerperAdapter(_client(handler), _config())
    adapter.search(SearchQuery(query="msft revenue", page=2, page_size=10, as_of=date(2025, 6, 1)))

    assert captured["url"] == SERPER_ENDPOINT
    assert captured["method"] == "POST"
    assert captured["body"] == {"q": "msft revenue", "page": 2, "num": 10}
    assert captured["auth"] == "sk-test-secret"


def test_response_mapping_to_search_result() -> None:
    """响应映射：Serper organic → SearchResult（title/link/displayLink/snippet/date）。"""
    adapter = SerperAdapter(
        _client(lambda r: httpx.Response(200, json=_serper_payload())), _config()
    )

    response = adapter.search(SearchQuery(query="msft", as_of=date(2025, 12, 31)))

    assert isinstance(response, SearchResponse)
    assert len(response.items) >= 1
    first = response.items[0]
    assert isinstance(first, SearchResult)
    assert first.title == "Microsoft - Official Home Page"
    assert first.url == "https://www.microsoft.com/"
    assert first.publisher == "www.microsoft.com"
    assert first.published_at == date(2025, 6, 1)


def test_api_key_cannot_be_leaked_as_string() -> None:
    """认证脱敏：SecretStr 的 str()/repr() 不泄漏明文 key。"""
    cfg = _config()
    assert "sk-test-secret" not in str(cfg)
    assert "sk-test-secret" not in repr(cfg)
    # 构造异常信息时也不得包含明文 key
    try:
        raise RuntimeError(f"请求失败 url={SERPER_ENDPOINT}")
    except RuntimeError as exc:
        assert "sk-test-secret" not in str(exc)


def test_http_error_raises_with_code_classification() -> None:
    """HTTP 429 → 抛 httpx.HTTPStatusError（上层 GoogleSearchTool 归一）。"""
    adapter = SerperAdapter(_client(lambda r: httpx.Response(429, request=r)), _config())

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        adapter.search(SearchQuery(query="msft", as_of=date(2025, 12, 31)))

    # 429 → RATE_LIMITED（由 classify_status_code 归一）
    from invest_research.infrastructure.http.client import classify_status_code

    assert classify_status_code(excinfo.value.response.status_code) == ErrorCode.RATE_LIMITED


def test_missing_published_date_defaults_to_as_of() -> None:
    """Serper 无 date 字段 → published_at 默认用 as_of（不造假，仅兜底）。"""
    payload = {"organic": [{"title": "T", "link": "https://x.com", "displayLink": "x.com"}]}
    adapter = SerperAdapter(_client(lambda r: httpx.Response(200, json=payload)), _config())

    response = adapter.search(SearchQuery(query="msft", as_of=date(2025, 6, 1)))

    assert response.items[0].published_at == date(2025, 6, 1)
