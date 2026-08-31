"""Outbox relay bootstrap（P05-03B）。

在 Worker/Celery 启动时调用 ``run_outbox_relay``，扫描未投递的
outbox 事件（pending）并投递给 JobDispatcher，解决"DB 创建 Job 成功
→ dispatch 失败 → Job 永久 pending"的窗口。

依赖注入：存储/dispatcher/计数器由调用方（wiring/worker）提供。
"""

from __future__ import annotations

import logging

from invest_research.application.outbox import OutboxRelayCounter, OutboxRelayService

logger = logging.getLogger(__name__)


def run_outbox_relay(
    relay: OutboxRelayService,
    counter: OutboxRelayCounter,
    *,
    limit: int | None = None,
) -> int:
    """启动时执行一次 outbox relay，返回本次成功投递数。

    - 只处理 pending 事件（已 sent/failed 不重复投递）；
    - 中途 dispatch 失败的事件回拨 pending 或转 failed，交给下次恢复；
    - 计数写入 counter 供 Prometheus gauge / 日志观测。
    """
    result = relay.relay_once(limit=limit)
    counter.record(result)
    logger.info(
        "outbox_relay_done",
        extra={
            "sent_count": result.sent_count,
            "checked_count": result.checked_count,
            "failed_count": result.failed_count,
        },
    )
    return result.sent_count
