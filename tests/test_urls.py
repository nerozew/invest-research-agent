"""P02-07 URL 规范化与来源去重测试（纯函数，不依赖网络）。"""

from __future__ import annotations

from invest_research.tools.urls import canonicalize_url, deduplicate_sources


def test_strips_tracking_params() -> None:
    """剔除 utm/gclid 等 tracking 参数。"""
    url = "https://example.com/news?utm_source=x&id=5&gclid=abc#section"
    assert canonicalize_url(url) == "https://example.com/news?id=5"


def test_lowercases_host_and_removes_default_port() -> None:
    """host 小写 + 移除默认端口（:443）。"""
    assert canonicalize_url("HTTPS://Example.COM:443/a") == "https://example.com/a"


def test_removes_fragment_and_blank() -> None:
    """去片段；空/空白输入返回 None。"""
    assert canonicalize_url("https://a.com/x#frag") == "https://a.com/x"
    assert canonicalize_url("") is None
    assert canonicalize_url("   ") is None


def test_sorts_query_params() -> None:
    """query 参数按键排序（canonical 稳定可比）。"""
    assert canonicalize_url("https://a.com/?b=2&a=1") == "https://a.com/?a=1&b=2"


def test_rejects_non_http_or_no_host() -> None:
    """非 http(s)/无 host 的 URL 返回 None。"""
    assert canonicalize_url("ftp://x.com/f") is None
    assert canonicalize_url("not a url") is None


def test_dedup_keeps_first_and_filters_invalid() -> None:
    """去重（保留首个）+ 剔除无效 URL。"""
    urls = [
        "https://example.com/x?utm_source=1",
        "https://example.com/x?utm_medium=m",
        "",  # 无效：空
        "not a url",  # 无效：无法解析
        "https://example.com/y",
    ]
    result = deduplicate_sources(urls)
    assert result == ["https://example.com/x", "https://example.com/y"]
