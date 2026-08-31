"""P05-02 尊重 Retry-After 并动态降速测试。

验证目标（对齐 docs/04-WORKFLOW-RELIABILITY.md §4/§5.1「优先尊重 Retry-After」）：
- ``parse_retry_after``：秒数格式（含小数）→ 等该秒数；
  HTTP-date 格式 → 等"目标时刻 - 当前时刻"秒（负数视为 0）；
  非法/缺失/负秒数 → None（回退指数退避）；不抛异常。
- ``ResponseHeadersRetryAfter``：从 httpx 响应头读取 ``retry-after``；
  缺失返回 None。
- ``build_retrying_with_retry_after``：有 Retry-After 用其等待；
  无 Retry-After 走指数退避。测试用 FakeSleep 记录等待，不依赖真实 sleep。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from invest_research.domain.errors import ErrorCode
from invest_research.infrastructure.retry import (
    ResponseHeadersRetryAfter,
    RetryableError,
    RetryPolicy,
    build_retrying_with_retry_after,
    parse_retry_after,
)


class FakeSleep:
    """记录每次 sleep 的时长，不真实等待。"""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class MutableRetryAfterProvider:
    """可切换 retry-after 值的测试提供方。"""

    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def get(self) -> str | None:
        return self.value


def _retryable() -> RetryableError:
    return RetryableError(ErrorCode.RATE_LIMITED, "模拟 429")


# ---------------------------------------------------------------------------
# parse_retry_after：秒数格式
# ---------------------------------------------------------------------------


def test_parse_seconds_format() -> None:
    """纯秒数 → 等该秒数。"""
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("30.5") == 30.5


def test_parse_seconds_ignores_whitespace() -> None:
    """首尾空白被清理。"""
    assert parse_retry_after("  45  ") == 45.0


def test_parse_negative_seconds_returns_none() -> None:
    """负秒数非法 → None（回退指数退避）。"""
    assert parse_retry_after("-5") is None


def test_parse_missing_or_blank_returns_none() -> None:
    """缺失/空白 → None。"""
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None


# ---------------------------------------------------------------------------
# parse_retry_after：HTTP-date 格式
# ---------------------------------------------------------------------------


def test_parse_http_date_format() -> None:
    """HTTP-date → 等"目标时刻 - 当前时刻"秒。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    target = now + timedelta(seconds=90)
    http_date = target.strftime("%a, %d %b %Y %H:%M:%S GMT")
    result = parse_retry_after(http_date, now=now)
    assert result is not None
    assert abs(result - 90.0) < 1.0


def test_parse_http_date_in_past_returns_zero() -> None:
    """目标时刻已过去 → 0 秒（立即重试）。"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    http_date = "Wed, 21 Oct 2015 07:28:00 GMT"
    assert parse_retry_after(http_date, now=now) == 0.0


def test_parse_invalid_returns_none() -> None:
    """非法值不抛异常、返回 None。"""
    assert parse_retry_after("not-a-date", now=datetime.now(timezone.utc)) is None
    assert parse_retry_after("abc-123", now=datetime.now(timezone.utc)) is None


# ---------------------------------------------------------------------------
# ResponseHeadersRetryAfter
# ---------------------------------------------------------------------------


def test_response_headers_retry_after_reads_header() -> None:
    """从响应头读取 retry-after（大小写不敏感）。"""
    provider = ResponseHeadersRetryAfter({"Retry-After": "10"})
    assert provider.get() == "10"


def test_response_headers_retry_after_missing_returns_none() -> None:
    """缺失响应头 → None。"""
    provider = ResponseHeadersRetryAfter({})
    assert provider.get() is None


# ---------------------------------------------------------------------------
# build_retrying_with_retry_after
# ---------------------------------------------------------------------------


def test_retry_after_used_when_present() -> None:
    """有 Retry-After 时每次重试等待其秒数（动态降速）。"""
    fake = FakeSleep()
    provider = MutableRetryAfterProvider(value="10")
    calls = 0

    def always_fails() -> str:
        nonlocal calls
        calls += 1
        raise _retryable()

    retrying = build_retrying_with_retry_after(
        provider,
        RetryPolicy(max_attempts=3, jitter_seconds=0.0),
        sleep=fake,
    )
    try:
        retrying(always_fails)
    except RetryableError:
        pass

    assert calls == 3
    assert fake.sleeps == [10.0, 10.0]


def test_exponential_backoff_used_when_header_missing() -> None:
    """无 Retry-After 时回退到指数退避 1s, 2s。"""
    fake = FakeSleep()
    provider = MutableRetryAfterProvider(value=None)
    calls = 0

    def always_fails() -> str:
        nonlocal calls
        calls += 1
        raise _retryable()

    retrying = build_retrying_with_retry_after(
        provider,
        RetryPolicy(max_attempts=3, jitter_seconds=0.0),
        sleep=fake,
    )
    try:
        retrying(always_fails)
    except RetryableError:
        pass

    assert calls == 3
    assert fake.sleeps == [1.0, 2.0]


def test_success_after_retry_after_header() -> None:
    """首次失败后按 Retry-After 等待，第二次成功。"""
    fake = FakeSleep()
    provider = MutableRetryAfterProvider(value="5")
    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _retryable()
        return "ok"

    retrying = build_retrying_with_retry_after(
        provider,
        RetryPolicy(max_attempts=3, jitter_seconds=0.0),
        sleep=fake,
    )
    assert retrying(flaky) == "ok"
    assert fake.sleeps == [5.0]
