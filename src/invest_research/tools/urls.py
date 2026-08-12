"""URL 规范化与来源去重（P02-07，纯函数）。

依据 docs/05 P02-07、docs/03-DATABASE sources.canonical_url 唯一：
- canonicalize_url()：去 tracking 参数、去片段、去默认端口、query 排序、小写 host；
- deduplicate_sources()：按 canonical_url 去重（保留首个），去掉纯空白/无效 URL。

不依赖网络；tracking 参数白名单来自常见 SEO/分析参数（可配置）。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 常见 tracking / 分析参数（去重时剔除）
DEFAULT_TRACKING_PARAMS: frozenset[str] = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
)

# URL 默认端口（canonical 时移除）
_DEFAULT_PORTS: dict[str, str] = {"http": "80", "https": "443"}


def canonicalize_url(
    url: str, tracking_params: frozenset[str] = DEFAULT_TRACKING_PARAMS
) -> str | None:
    """规范化 URL：去空白、片段、按端口、tracking 参数；query 按键排序。

    返回规范化后的 URL 字符串；无法解析/空输入返回 None。
    """
    if not url or not url.strip():
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    # host 小写 + 去默认端口
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = str(parsed.port) if parsed.port else ""
    if port == _DEFAULT_PORTS.get(parsed.scheme):
        port = ""
    netloc = f"{host}:{port}" if port else host

    # 去 tracking 参数 + query 按键排序（保留重复键）
    filtered = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in tracking_params
    ]
    query = urlencode(sorted(filtered))

    return urlunparse((parsed.scheme, netloc, parsed.path or "/", parsed.params, query, ""))


def deduplicate_sources(urls: list[str]) -> list[str]:
    """按 canonical_url 去重（保留首个），去除无法规范化的 URL。

    返回去重后的规范化 URL 列表（保持首次出现顺序）。
    """
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        canon = canonicalize_url(url)
        if canon is None or canon in seen:
            continue
        seen.add(canon)
        result.append(canon)
    return result
