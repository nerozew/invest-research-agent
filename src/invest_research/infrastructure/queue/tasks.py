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
from typing import Protocol

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub

__all__ = ["JobTaskHandler", "TASK_PROCESS_JOB", "register_tasks"]

# 任务名：worker 与 producer 共享的稳定契约。
TASK_PROCESS_JOB = "invest_research.process_research_job"


class JobTaskHandler(Protocol):
    """处理一个 job 的端口（application 层实现，P04-07 接入 Flow）。"""

    def process(self, job_id: uuid.UUID) -> None: ...


def register_tasks(app: Celery, handler: JobTaskHandler) -> Celery:
    """把 fake/真实 handler 注册为 celery task（P04-06 最小 worker）。

    使用 ``app.task`` 而不是模块级装饰器：handler 可注入（测试用 fake），
    且只在 ``register_tasks`` 显式调用时注册——保持模块导入零副作用。
    """

    @app.task(  # type: ignore[untyped-decorator]
        name=TASK_PROCESS_JOB, bind=True
    )
    def _process_research_job(self: object, job_id: str) -> str:
        """消费队列中的 job_id（worker 侧）。"""
        handler.process(uuid.UUID(job_id))
        return job_id

    return app
