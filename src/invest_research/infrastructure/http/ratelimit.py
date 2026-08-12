"""全局限流器（Token Bucket，P02-03）。

依据 docs/04-WORKFLOW-RELIABILITY.md §5.2 SEC 合规：
- 永不并发突破项目限速；本项目安全上限 5 req/s（低于官方 10 req/s）。

设计：
- ``SleepableClock``：可注入的时钟接口，测试用 ``FakeClock`` 推进时间，
  避免真实 sleep 拖慢测试（配合 docs/04 §5.1「测试不得依赖真实等待时间」）；
- ``TokenBucketRateLimiter``：经典 token bucket —— 桶容量 = 每秒速率，
  每 acquire 消耗 1 个 token；token 按速率随时间补充，有上限。

依赖边界：纯 Python，不依赖 httpx/任何框架。
"""

from __future__ import annotations

import time
from typing import Protocol


class SleepableClock(Protocol):
    """可注入时钟：提供当前时间与睡眠（真实或模拟）。"""

    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """真实时钟：委托 time.time 与 time.sleep。"""

    def time(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class TokenBucketRateLimiter:
    """Token Bucket 限流器（固定速率、突发上限 = 桶容量）。

    - ``rate_per_second``：每秒补充的 token 数（并为桶容量上限）；
    - ``acquire()``：有 token 立即返回 True 并消耗；否则返回 False。
      调用方拿到 False 后自行等待/重试，不阻塞（适合压测与可控重试）。
    """

    def __init__(self, rate_per_second: float, clock: SleepableClock | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second 必须为正数")
        self._rate = rate_per_second
        self._clock: SleepableClock = clock if clock is not None else RealClock()
        self._tokens = rate_per_second  # 初始满桶，允许突发
        self._last = self._clock.time()

    @property
    def rate_per_second(self) -> float:
        return self._rate

    def _refill(self) -> None:
        """按经过时间补充 token，上限为桶容量（= rate）。"""
        now = self._clock.time()
        elapsed = now - self._last
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._last = now

    def acquire(self) -> bool:
        """尝试获取一个 token。成功返回 True；超限返回 False（不阻塞）。"""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
