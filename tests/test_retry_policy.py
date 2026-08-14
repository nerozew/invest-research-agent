"""P05-01 通用 retry policy 测试。

验证目标（docs/04-WORKFLOW-RELIABILITY.md §4/§5.1）：
- 只重试白名单错误（RATE_LIMITED/NETWORK_TRANSIENT/UPSTREAM_5XX/SCHEMA_INVALID）；
- 非白名单错误（INPUT_INVALID/AUTH_ERROR 等）不能包装为 RetryableError；
- 重试次数有上限，耗尽后抛出最后一次原始异常（不吞错）；
- 退避时长按指数增长并受上限约束；
- 注入 FakeSleep 验证等待时长，不依赖真实 sleep。
"""

from __future__ import annotations

import random

import pytest

from invest_research.domain.errors import ErrorCode
from invest_research.infrastructure.retry import (
    DEFAULT_BASE_WAIT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    RetryableError,
    RetryPolicy,
    build_retrying,
    compute_backoff_delay,
    retry_call,
)


class FakeSleep:
    """记录每次 sleep 的时长，不真实等待。"""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _retryable(code: ErrorCode) -> RetryableError:
    return RetryableError(code, f"模拟 {code}")


# ---------------------------------------------------------------------------
# RetryableError：白名单校验
# ---------------------------------------------------------------------------


def test_retryable_error_accepts_whitelisted_codes() -> None:
    """白名单错误码都可以构造 RetryableError。"""
    for code in (
        ErrorCode.RATE_LIMITED,
        ErrorCode.NETWORK_TRANSIENT,
        ErrorCode.UPSTREAM_5XX,
        ErrorCode.SCHEMA_INVALID,
    ):
        exc = RetryableError(code, "x")
        assert exc.error_code == code


def test_retryable_error_rejects_non_retryable_codes() -> None:
    """非白名单错误码构造 RetryableError 必须抛 ValueError。"""
    for code in (
        ErrorCode.INPUT_INVALID,
        ErrorCode.AUTH_ERROR,
        ErrorCode.COMPANY_AMBIGUOUS,
        ErrorCode.INTERNAL_BUG,
    ):
        with pytest.raises(ValueError):
            RetryableError(code, "x")


# ---------------------------------------------------------------------------
# compute_backoff_delay：指数退避 + 上限 + 抖动
# ---------------------------------------------------------------------------


def test_backoff_exponential_growth() -> None:
    """退避按 2 的幂增长：1s, 2s, 4s, 8s。"""
    assert compute_backoff_delay(1, jitter_seconds=0.0) == 1.0
    assert compute_backoff_delay(2, jitter_seconds=0.0) == 2.0
    assert compute_backoff_delay(3, jitter_seconds=0.0) == 4.0
    assert compute_backoff_delay(4, jitter_seconds=0.0) == 8.0


def test_backoff_respects_max_wait() -> None:
    """退避时长被上限截断：base=1, max=5 → 超过后固定为 5。"""
    assert compute_backoff_delay(3, base_wait=1.0, max_wait=5.0, jitter_seconds=0.0) == 4.0
    assert compute_backoff_delay(4, base_wait=1.0, max_wait=5.0, jitter_seconds=0.0) == 5.0
    assert compute_backoff_delay(5, base_wait=1.0, max_wait=5.0, jitter_seconds=0.0) == 5.0


def test_backoff_jitter_deterministic_with_seed() -> None:
    """固定 seed 的 rng 产生确定抖动；无 rng 时使用全局 random。"""
    rng = random.Random(42)
    a = compute_backoff_delay(2, jitter_seconds=1.0, rng=rng)
    rng = random.Random(42)
    b = compute_backoff_delay(2, jitter_seconds=1.0, rng=rng)
    assert a == b
    # 抖动范围在 [0, 1)
    assert 2.0 <= a < 3.0


def test_backoff_invalid_arguments() -> None:
    """非法参数必须抛 ValueError。"""
    with pytest.raises(ValueError):
        compute_backoff_delay(0)
    with pytest.raises(ValueError):
        compute_backoff_delay(1, base_wait=0.0)
    with pytest.raises(ValueError):
        compute_backoff_delay(1, max_wait=0.0)
    with pytest.raises(ValueError):
        compute_backoff_delay(1, jitter_seconds=-0.1)


# ---------------------------------------------------------------------------
# RetryPolicy：参数校验
# ---------------------------------------------------------------------------


def test_retry_policy_defaults() -> None:
    """默认策略：4 次尝试、base 1s、max 30s、jitter 0.5s。"""
    policy = RetryPolicy()
    assert policy.max_attempts == DEFAULT_MAX_ATTEMPTS
    assert policy.base_wait_seconds == DEFAULT_BASE_WAIT_SECONDS
    assert policy.max_wait_seconds == 30.0


def test_retry_policy_rejects_invalid() -> None:
    """非法策略参数必须抛 ValueError。"""
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_wait_seconds=0.0)
    with pytest.raises(ValueError):
        RetryPolicy(max_wait_seconds=0.0)
    with pytest.raises(ValueError):
        RetryPolicy(max_wait_seconds=0.5, base_wait_seconds=1.0)  # max < base
    with pytest.raises(ValueError):
        RetryPolicy(jitter_seconds=-1.0)


# ---------------------------------------------------------------------------
# build_retrying / retry_call：只重试白名单错误
# ---------------------------------------------------------------------------


def test_retries_whitelisted_error_then_succeeds() -> None:
    """白名单错误重试后成功：调用 2 次（失败1 + 成功1），sleeps 记录 1 次。"""
    fake = FakeSleep()

    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _retryable(ErrorCode.NETWORK_TRANSIENT)
        return "ok"

    result = retry_call(flaky, RetryPolicy(max_attempts=4, jitter_seconds=0.0), sleep=fake)
    assert result == "ok"
    assert calls == 2
    assert fake.sleeps == [1.0]  # 第 1 次重试等待 1s


def test_stops_after_max_attempts_and_reraises() -> None:
    """重试次数耗尽后必须抛出最后一次原始 RetryableError。"""
    fake = FakeSleep()
    calls = 0

    def always_fails() -> str:
        nonlocal calls
        calls += 1
        raise _retryable(ErrorCode.UPSTREAM_5XX)

    with pytest.raises(RetryableError) as exc_info:
        retry_call(always_fails, RetryPolicy(max_attempts=3, jitter_seconds=0.0), sleep=fake)

    assert exc_info.value.error_code == ErrorCode.UPSTREAM_5XX
    assert calls == 3  # 初始 1 次 + 2 次重试
    assert fake.sleeps == [1.0, 2.0]  # 等待 1s, 2s


def test_non_retryable_error_is_not_retried() -> None:
    """非 RetryableError（普通异常）不被重试：只调用 1 次。"""
    fake = FakeSleep()
    calls = 0

    def raises_plain() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("不是 RetryableError")

    with pytest.raises(RuntimeError):
        retry_call(raises_plain, RetryPolicy(jitter_seconds=0.0), sleep=fake)

    assert calls == 1
    assert fake.sleeps == []


def test_success_on_first_attempt_no_sleep() -> None:
    """第一次就成功时不 sleep。"""
    fake = FakeSleep()
    assert retry_call(lambda: "done", RetryPolicy(jitter_seconds=0.0), sleep=fake) == "done"
    assert fake.sleeps == []


def test_backoff_sequence_is_recorded() -> None:
    """连续失败时退避序列为 1s, 2s, 4s（默认 4 次尝试）。"""
    fake = FakeSleep()
    calls = 0

    def always_fails() -> str:
        nonlocal calls
        calls += 1
        raise _retryable(ErrorCode.RATE_LIMITED)

    with pytest.raises(RetryableError):
        retry_call(always_fails, RetryPolicy(max_attempts=4, jitter_seconds=0.0), sleep=fake)

    assert calls == 4
    assert fake.sleeps == [1.0, 2.0, 4.0]


def test_build_retrying_returns_retrying() -> None:
    """build_retrying 返回可复用的 tenacity Retrying 实例。"""
    fake = FakeSleep()
    retrying = build_retrying(RetryPolicy(max_attempts=2, jitter_seconds=0.0), sleep=fake)
    assert retrying is not None

    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _retryable(ErrorCode.SCHEMA_INVALID)
        return "ok"

    assert retrying(flaky) == "ok"
    assert calls == 2
