"""P02-03 全局限流器（Token Bucket）测试。

验证目标（docs/04-WORKFLOW-RELIABILITY.md §5.2）：
- 匀速请求不超限：按速率消耗后在规定速率窗口内保持允许；
- 突发请求被限：连续超过桶容量（= rate）的 acquire 返回 False；
- 时间推进后恢复：经过足够时间 token 补充，又可 acquire；
- 默认速率参数生效：不同 rate 下行为不同；
- 非法速率被拒：rate<=0 抛 ValueError。

使用 FakeClock 注入时间，不依赖真实 sleep（docs/04 §5.1：测试不依赖真实等待时间）。
"""

from __future__ import annotations

import pytest

from invest_research.infrastructure.http.ratelimit import TokenBucketRateLimiter


class FakeClock:
    """模拟时钟：构造时记录 start，sleep() 推进流逝时间（秒），time() 返回单调时间。"""

    def __init__(self) -> None:
        self._now = 0.0
        self._start = 0.0

    def time(self) -> float:
        return self._start + self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# acquire 基础行为
# ---------------------------------------------------------------------------


def test_initial_burst_allows_up_to_capacity() -> None:
    """初始满桶：速率=3 时连续 3 次 acquire 成功，第 4 次失败。"""
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(rate_per_second=3.0, clock=clock)

    assert [limiter.acquire() for _ in range(3)] == [True, True, True]
    assert limiter.acquire() is False


def test_acquire_refills_after_time_passes() -> None:
    """时间推进 1 秒后 token 恢复：速率=3 → 推进 1s 可再获得 3 个 token。"""
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(rate_per_second=3.0, clock=clock)

    # 耗尽突发配额
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is False

    # 推进 1 秒 → 补充 3 个 token
    clock.sleep(1.0)
    assert [limiter.acquire() for _ in range(3)] == [True, True, True]
    assert limiter.acquire() is False


def test_steady_rate_never_exceeds_limit() -> None:
    """匀速请求（每 1/rate 秒一次）永不被限。"""
    clock = FakeClock()
    rate = 5.0
    limiter = TokenBucketRateLimiter(rate_per_second=rate, clock=clock)

    results: list[bool] = []
    for _ in range(20):
        results.append(limiter.acquire())
        clock.sleep(1.0 / rate)  # 每 0.2s 一次（5 req/s）
    assert all(results)


def test_burst_then_recovery_loop() -> None:
    """突发 → 超限 → 恢复 → 可继续 的循环语义。"""
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(rate_per_second=2.0, clock=clock)

    # 突发 2 次成功，第 3 次被限
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is False

    # 推进 0.5s → 补充 1 token → 又能取 1 次
    clock.sleep(0.5)
    assert limiter.acquire() is True
    assert limiter.acquire() is False


def test_custom_rate_affects_capacity() -> None:
    """速率参数决定桶容量：rate=1 只能突发 1 次。"""
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(rate_per_second=1.0, clock=clock)

    assert limiter.rate_per_second == 1.0
    assert limiter.acquire() is True
    assert limiter.acquire() is False


def test_rejects_non_positive_rate() -> None:
    """非法速率（<=0）必须被拒绝。"""
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate_per_second=0.0, clock=FakeClock())
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate_per_second=-1.0, clock=FakeClock())


def test_uses_real_clock_by_default() -> None:
    """未注入时钟时使用真实时钟（RealClock），不抛错。"""
    limiter = TokenBucketRateLimiter(rate_per_second=5.0)
    assert limiter.acquire() is True
