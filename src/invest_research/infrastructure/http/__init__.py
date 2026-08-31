"""HTTP 基础设施包（P02-02）。

提供共享的 httpx client 工厂与 HTTP 异常/状态码 → ErrorCode 的统一映射。

依赖边界：本层只依赖标准库、httpx 与 domain 层；
禁止导入 CrewAI、FastAPI、SQLAlchemy 或任何供应商 SDK。
"""

from __future__ import annotations

from invest_research.infrastructure.http.client import (
    build_http_client,
    classify_http_exception,
    classify_status_code,
)
from invest_research.infrastructure.http.ratelimit import (
    RealClock,
    SleepableClock,
    TokenBucketRateLimiter,
)

__all__ = [
    "build_http_client",
    "classify_http_exception",
    "classify_status_code",
    "RealClock",
    "SleepableClock",
    "TokenBucketRateLimiter",
]
