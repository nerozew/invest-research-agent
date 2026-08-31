"""Worker 启动自动 stale recovery（P05-03A）。

在 Worker/Celery 启动流程中调用 ``run_startup_recovery``，
自动扫描超过 lease 的 ``running`` 步骤并标记为可重试，
同时把恢复计数暴露给日志/Prometheus 观测。

设计（at-least-once 启动语义）：
- 每次 worker 启动执行一次恢复扫描（不是只有崩溃后手动跑）；
- 恢复是安全的：条件更新（仅当仍是 running 才标记），不会覆盖
  正在真实执行的慢步骤；
- 恢复计数（``RecoveryCounter``）累计每次启动的恢复数，供
  ``/metrics`` gauge（P05-06）与结构化日志使用。

依赖边界：本层只允许导入标准库、application 与 domain；
存储实例由调用方（wiring/worker）注入。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from invest_research.application.recovery import (
    RecoveryCounter,
    StaleRecoveryService,
)

logger = logging.getLogger(__name__)

ClockNow = Callable[[], datetime]


def run_startup_recovery(
    recovery_service: StaleRecoveryService,
    counter: RecoveryCounter,
    *,
    now: ClockNow | None = None,
) -> int:
    """Worker 启动时执行一次 stale recovery，返回恢复的步骤数。

    - 调用 ``recovery_service.recover(now=now)`` 完成扫描与恢复；
    - 把结果写入 ``counter``（累计计数 + 最近一次扫描）；
    - P06-06C：按本次真实恢复数更新 ``stale_running_steps`` Gauge
      （指标写入失败由 metrics_events 脱敏处理，不影响恢复流程）；
    - 返回本次恢复数（供调用方决定是否记录日志/更新 gauge）。
    """
    result = recovery_service.recover(now=now() if now is not None else None)
    counter.record(result)
    # P06-06C：把本次扫描恢复的 stale 步骤数设置为当前 Gauge 值。
    from invest_research.infrastructure.observability.metrics_events import (
        set_stale_running_steps,
    )

    set_stale_running_steps(result.recovered_count)
    logger.info(
        "startup_recovery_done",
        extra={
            "recovered_count": result.recovered_count,
            "checked_count": result.checked_count,
        },
    )
    return result.recovered_count
