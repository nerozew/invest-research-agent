"""通用 retry policy（P05-01 / P05-02）。

依据 `docs/04-WORKFLOW-RELIABILITY.md`：
- §5.1 推荐默认值：网络/429/5xx 最多 4 次尝试，等待 `1s, 2s, 4s, 8s`、
  上限 30 秒并加抖动；优先尊重 `Retry-After`；
- §4 错误分类：只有 `RATE_LIMITED` / `NETWORK_TRANSIENT` / `UPSTREAM_5XX`
  （及限定次数的 `SCHEMA_INVALID`）允许自动重试；其它错误立即终态失败。

设计：
- ``RetryableError``：把统一 ``ErrorCode`` 包装成可重试异常。构造时校验错误码
  必须位于 domain 白名单内，防止把不可重试错误包装成可重试；
- ``compute_backoff_delay``：纯函数，计算第 N 次重试前的等待秒数
  （指数退避 + 上限 + 可选抖动），可用固定 seed 的 ``random.Random`` 复现；
- ``parse_retry_after``：解析 ``Retry-After`` 响应头（优先秒数、其次 HTTP-date），
  供 429/503 等服务指引重试时机（P05-02）；
- ``RetryPolicy``：冻结数据类，集中管理重试参数；
- ``build_retrying`` / ``build_retrying_with_retry_after``：基于 tenacity 构造
  ``Retrying`` 实例，只对 ``RetryableError`` 重试；``sleep`` 可注入，
  测试用 FakeClock 记录/推进等待时长，不依赖真实 sleep（docs/04 §5.1）。

依赖边界：本层只允许导入标准库、tenacity 与 domain 层。
"""

from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, TypeVar

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt

from invest_research.domain.errors import ErrorCode, is_retryable

T = TypeVar("T")

# 可靠性 §5.1：网络/429/5xx 最多 4 次尝试（初始 1 次 + 3 次重试）
DEFAULT_MAX_ATTEMPTS = 4
# 退避：1s, 2s, 4s, 8s …，上限 30s（§5.1）
DEFAULT_BASE_WAIT_SECONDS = 1.0
DEFAULT_MAX_WAIT_SECONDS = 30.0
# 默认抖动范围：0 表示无抖动（测试默认确定性）
DEFAULT_JITTER_SECONDS = 0.5


class RetryableError(Exception):
    """可重试错误：携带统一 ErrorCode，按 domain 白名单决定是否重试。

    构造时要求 ``error_code`` 必须位于可重试白名单内；非白名单错误码
    （如 ``AUTH_ERROR``、``INPUT_INVALID``）不能被包装为可重试错误。
    """

    def __init__(self, error_code: ErrorCode, message: str) -> None:
        code = ErrorCode(error_code)
        if not is_retryable(code):
            raise ValueError(f"不可重试错误码不能作为 RetryableError: {code}")
        super().__init__(message)
        self.error_code = code
        self.message = message


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """解析 HTTP ``Retry-After`` 响应头，返回需要等待的秒数。

    兼容两种格式（RFC 9110 §10.2.3）：
    - 秒数：``Retry-After: 120``；
    - HTTP-date：``Retry-After: Wed, 21 Oct 2015 07:28:00 GMT``
      （此时需要等待"该时刻 - 当前时间"秒，负数视为 0）。

    非法/缺失返回 ``None``（等待时长不确定，交由上层指数退避兜底）；
    不抛异常——响应头是人类可写的，永远不应因坏头导致调用失败。
    """
    if value is None or not value.strip():
        return None
    raw = value.strip()

    # 1) 秒数格式：纯数字（含小数）
    try:
        seconds = float(raw)
        if seconds < 0:
            return None
        return seconds
    except ValueError:
        pass

    # 2) HTTP-date 格式（用 email.utils 解析）
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    # 统一为 UTC 以便比较
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delta = (parsed - current).total_seconds()
    return max(0.0, delta)


class RetryAfterProvider:
    """从外部状态提供 ``Retry-After`` 值的抽象（测试可注入）。"""

    def get(self) -> str | None:
        raise NotImplementedError


class ResponseHeadersRetryAfter(RetryAfterProvider):
    """从 httpx 响应头读取 ``Retry-After``（大小写不敏感）。

    httpx 的 ``response.headers`` 本身大小写不敏感；此处兼容普通 dict：
    遍历键做不区分大小写的匹配。
    """

    def __init__(self, response: Mapping[str, str]) -> None:
        self._headers = response

    def get(self) -> str | None:
        for key, value in self._headers.items():
            if key.lower() == "retry-after":
                return value
        return None


def compute_backoff_delay(
    attempt: int,
    *,
    base_wait: float = DEFAULT_BASE_WAIT_SECONDS,
    max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    jitter_seconds: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """计算第 ``attempt`` 次重试前的等待秒数。

    - 指数退避：``base_wait * 2 ** (attempt - 1)``，上限 ``max_wait``；
    - 抖动：当 ``jitter_seconds > 0`` 时叠加 ``[0, jitter_seconds]`` 均匀随机；
      传入固定 seed 的 ``rng`` 可复现（测试用）。
    """
    if attempt < 1:
        raise ValueError("attempt 必须 >= 1")
    if base_wait <= 0 or max_wait <= 0:
        raise ValueError("base_wait/max_wait 必须为正数")
    if jitter_seconds < 0:
        raise ValueError("jitter_seconds 不能为负数")

    delay: float = min(base_wait * (2 ** (attempt - 1)), max_wait)
    if jitter_seconds > 0:
        if rng is not None:
            delay += float(rng.uniform(0.0, jitter_seconds))
        else:
            delay += float(random.uniform(0.0, jitter_seconds))
    return delay


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略参数（与可靠性 §5.1 默认值对齐）。"""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_wait_seconds: float = DEFAULT_BASE_WAIT_SECONDS
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    jitter_seconds: float = DEFAULT_JITTER_SECONDS

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        if self.base_wait_seconds <= 0 or self.max_wait_seconds <= 0:
            raise ValueError("等待秒数必须为正数")
        if self.max_wait_seconds < self.base_wait_seconds:
            raise ValueError("max_wait_seconds 不得小于 base_wait_seconds")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds 不能为负数")


def build_retrying(
    policy: RetryPolicy = RetryPolicy(),
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Retrying:
    """构造 tenacity ``Retrying`` 实例。

    - 只对 ``RetryableError`` 重试（错误码白名单在 domain 层判定）；
    - 指数退避 + 上限 + 抖动由 ``compute_backoff_delay`` 计算；
    - ``sleep`` 可注入（测试用 FakeClock.sleep），生产用 ``time.sleep``；
    - ``reraise=True``：重试次数耗尽后抛出最后一次原始异常，不吞错。
    """

    def _wait(rs: object) -> float:
        # rs 为 tenacity.RetryCallState；attempt_number 表示当前重试次数（从 1 开始）
        attempt = getattr(rs, "attempt_number")
        return compute_backoff_delay(
            attempt,
            base_wait=policy.base_wait_seconds,
            max_wait=policy.max_wait_seconds,
            jitter_seconds=policy.jitter_seconds,
        )

    return Retrying(
        stop=stop_after_attempt(policy.max_attempts),
        wait=_wait,
        retry=retry_if_exception_type(RetryableError),
        reraise=True,
        sleep=sleep,
    )


def build_retrying_with_retry_after(
    retry_after_provider: RetryAfterProvider,
    policy: RetryPolicy = RetryPolicy(),
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] | None = None,
) -> Retrying:
    """构造 ``Retrying``：单次重试前优先使用 ``Retry-After``，否则指数退避。

    语义（docs/04 §5.1「优先尊重 Retry-After」）：
    - 提供方返回合法 ``Retry-After`` → 等待其秒数（动态降速，配合上游限流）；
    - 无/非法 ``Retry-After`` → 回退到 ``compute_backoff_delay`` 指数退避；
    - 只对 ``RetryableError`` 重试；``reraise=True``；``sleep`` 可注入。
    """

    def _wait(rs: object) -> float:
        attempt = getattr(rs, "attempt_number")
        raw = retry_after_provider.get()
        retry_after = parse_retry_after(raw, now=clock() if clock is not None else None)
        if retry_after is not None:
            return retry_after
        return compute_backoff_delay(
            attempt,
            base_wait=policy.base_wait_seconds,
            max_wait=policy.max_wait_seconds,
            jitter_seconds=policy.jitter_seconds,
        )

    return Retrying(
        stop=stop_after_attempt(policy.max_attempts),
        wait=_wait,
        retry=retry_if_exception_type(RetryableError),
        reraise=True,
        sleep=sleep,
    )


def retry_call(
    fn: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """同步调用 ``fn`` 并套用重试策略（工具脚本/测试的便捷入口）。"""
    return build_retrying(policy, sleep=sleep)(fn)
