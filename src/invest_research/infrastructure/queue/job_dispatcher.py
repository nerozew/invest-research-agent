"""Celery 生产的任务投递实现（P04-10A API→Worker 链路）。

用 ``celery_app.send_task`` 把 job_id 投递到稳定任务名
``invest_research.process_research_job``；仅 JSON 序列化（Celery app 已禁 pickle）。

模块导入零 Redis 连接：只在 dispatch() 被调用时 send_task（连接 Redis）。
"""

from __future__ import annotations

import uuid

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub

from invest_research.infrastructure.queue.tasks import TASK_PROCESS_JOB

__all__ = ["CeleryJobDispatcher"]


class CeleryJobDispatcher:
    """把 job_id 投递到 Celery 队列的生产实现。"""

    def __init__(self, celery_app: Celery) -> None:
        self._app = celery_app

    def dispatch(self, job_id: uuid.UUID) -> None:
        """投递 job_id（字符串）到 research-jobs 队列。失败抛异常由调用方记录。"""
        self._app.send_task(
            TASK_PROCESS_JOB,
            args=[str(job_id)],
            queue="research-jobs",
        )
