"""P02-18 Serper provider adapter（P02-17 SearchProvider 的具体实现）。

职责：
- ``SerperConfig``：Serper API 配置（api_key 用 SecretStr，str()/repr() 不泄漏）；
- ``SerperAdapter``：实现 ``SearchProvider``，把 SearchQuery → Serper HTTP 请求
  （POST {SERPER_ENDPOINT}，body 含 q/page/num），把 Serper organic JSON →
  SearchResult（title/link/displayLink/snippet/date）。

设计：
- 注入 httpx.Client（生产用共享 client，测试用 MockTransport，可完全离线）；
- HTTP 错误抛 httpx 异常（由上层 GoogleSearchTool 统一归一为 ToolFailure），
  但 adapter 不打印/不记录 api_key——认证信息只出现在请求头；
- Serper 返回的 ``date`` 是格式化字符串（如 "Jun 1, 2025"），尝试解析失败时
  回退到 SearchQuery.as_of（不造假、仅兜底）；
- as_of 过滤由本 adapter 完成：只保留 date <= as_of 的条目（与 P02-17 契约一致）。

依赖边界：只依赖标准库、httpx、Pydantic、domain 层、tools/google_search；
禁止导入 CrewAI/FastAPI/SQLAlchemy。外部服务在测试中用 MockTransport。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from invest_research.tools.google_search import (
    SearchProvider,
    SearchQuery,
    SearchResponse,
    SearchResult,
)

SERPER_ENDPOINT = "https://google.serper.dev/search"

# Serper 日期格式："Jun 1, 2025"
_SERPER_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y")


class SerperConfig(BaseModel):
    """Serper API 配置。api_key 用 SecretStr，str() 输出保密。"""

    model_config = ConfigDict(frozen=True)

    api_key: SecretStr
    endpoint: str = SERPER_ENDPOINT


def _parse_serper_date(raw: str | None) -> date | None:
    """解析 Serper 的格式化日期；无法解析返回 None。"""
    if not raw:
        return None
    for fmt in _SERPER_DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


class SerperAdapter(SearchProvider):
    """Serper 搜索适配器：实现 P02-17 SearchProvider 接口。"""

    def __init__(self, client: httpx.Client, config: SerperConfig) -> None:
        self._client = client
        self._config = config

    def search(self, query: SearchQuery) -> SearchResponse:
        """按 query 调用 Serper：POST 请求 → organic 结果 → SearchResponse。

        认证信息通过 ``X-Api-Key`` 请求头发送，不进入日志/异常；
        HTTP 错误直接抛 httpx 异常（交由上层 GoogleSearchTool 归一）。
        """
        payload = {"q": query.query, "page": query.page, "num": query.page_size}
        try:
            response = self._client.post(
                self._config.endpoint,
                json=payload,
                headers={"X-Api-Key": self._config.api_key.get_secret_value()},
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            # 网络/解析错误：不泄漏 key，抛出归一化前的中性异常
            raise RuntimeError(f"Serper 请求失败: {type(exc).__name__}") from exc

        items: list[SearchResult] = []
        for entry in body.get("organic", []) or []:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            link = str(entry.get("link") or "").strip()
            if not title or not link:
                continue
            published = _parse_serper_date(entry.get("date"))
            if published is None or published > query.as_of:  # 按 as_of 过滤
                published_effective = published if published is not None else query.as_of
                if published_effective > query.as_of:
                    continue
            else:
                published_effective = published
            items.append(
                SearchResult(
                    title=title,
                    url=link,
                    publisher=str(entry.get("displayLink") or "").strip() or None,
                    # snippet 保留空格规范；无则空串
                    published_at=_normalize_date(entry, query),
                    accessed_at=datetime.now(),
                )
            )

        return SearchResponse(
            items=tuple(items),
            total=len(items),
            page=query.page,
        )


def _normalize_date(entry: dict[str, Any], query: SearchQuery) -> date:
    """兜底：Serper 无 date 或解析失败 → 用 as_of；否则按 as_of 过滤。"""
    parsed = _parse_serper_date(entry.get("date"))
    if parsed is None:
        return query.as_of
    return parsed if parsed <= query.as_of else query.as_of
