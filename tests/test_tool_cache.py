"""P05.5-3 ToolCallCache 单元测试（纯函数，无外部依赖）。"""

from __future__ import annotations

from invest_research.infrastructure.tool_cache import ToolCallCache


def test_key_normalizes_param_order() -> None:
    """相同参数不同顺序 → 相同键（键排序）。"""
    cache = ToolCallCache()
    k1 = cache.key("sec_submissions", {"cik": "1", "as_of_date": "2025-01-01"})
    k2 = cache.key("sec_submissions", {"as_of_date": "2025-01-01", "cik": "1"})
    assert k1 == k2


def test_key_differs_by_tool_and_params() -> None:
    """工具名或参数不同 → 不同键。"""
    cache = ToolCallCache()
    assert cache.key("a", {"x": 1}) != cache.key("b", {"x": 1})
    assert cache.key("a", {"x": 1}) != cache.key("a", {"x": 2})


def test_key_normalizes_non_json_types() -> None:
    """日期等非 JSON 类型经 default=str 规范化。"""
    from datetime import date

    cache = ToolCallCache()
    k1 = cache.key("web_search", {"as_of": date(2025, 1, 1)})
    k2 = cache.key("web_search", {"as_of": "2025-01-01"})
    # date 序列化为 ISO 字符串后与字符串参数一致
    assert "2025-01-01" in k1
    assert k1 == k2


def test_put_get_roundtrip() -> None:
    cache = ToolCallCache()
    key = cache.key("sec_submissions", {"cik": "1"})
    assert cache.get(key) is None
    cache.put(key, '{"ok": true}')
    assert cache.get(key) == '{"ok": true}'
    assert cache.has(key) is True


def test_get_missing_returns_none() -> None:
    cache = ToolCallCache()
    assert cache.get("missing:key") is None
    assert cache.has("missing:key") is False
