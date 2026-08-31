"""Celery worker 任务（P04-06：最小 worker）。

P04-06 只做"消息投递 → worker 消费"验证：
- task 接收 ``job_id``（字符串）；
- 通过可注入的 ``JobTaskHandler``（application 端口）执行实际工作；
- P04-07 会把 handler 换成"加载任务 + 调用 Flow"的真实 adapter；
  本任务用 fake handler 验证投递→消费链路。

模块导入零 Redis 连接：Celery app 由 ``create_celery_app`` 显式创建
（见 celery_app.py）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Protocol

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub

from invest_research.infrastructure.queue.constants import TASK_PROCESS_JOB

__all__ = ["JobTaskHandler", "TASK_PROCESS_JOB", "register_tasks"]


class JobTaskHandler(Protocol):
    """处理一个 job 的端口（application 层实现，P04-07 接入 Flow）。"""

    def process(self, job_id: uuid.UUID) -> None: ...


def register_tasks(app: Celery, handler: JobTaskHandler) -> Celery:
    """把 fake/真实 handler 注册为 celery task（P04-06 最小 worker）。

    使用 ``app.task`` 而不是模块级装饰器：handler 可注入（测试用 fake），
    且只在 ``register_tasks`` 显式调用时注册——保持模块导入零副作用。
    """

    # Celery apps created in one Python process can inherit a previously
    # finalized task with the same global name. Re-registration must bind the
    # handler passed to *this* app (important for test isolation and worker
    # reloads), rather than silently keeping a stale closure.
    if TASK_PROCESS_JOB in app.tasks:
        app.tasks.unregister(TASK_PROCESS_JOB)

    @app.task(  # type: ignore[untyped-decorator]
        name=TASK_PROCESS_JOB, bind=True
    )
    def _process_research_job(self: object, job_id: str) -> str:
        """消费队列中的 job_id（worker 侧，P06-05 包在 worker.process span 内）。"""
        from invest_research.infrastructure.observability.tracing import (
            extract_trace_context,
            span,
        )

        request = getattr(self, "request", None)
        raw_headers = getattr(request, "headers", None)
        headers = raw_headers if isinstance(raw_headers, Mapping) else None
        parent = extract_trace_context(dict(headers) if headers is not None else None)
        with span("worker.process", {"job_id": job_id}, context=parent):
            handler.process(uuid.UUID(job_id))
        return job_id

    return app
