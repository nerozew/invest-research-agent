"""Celery app 工厂与最小队列配置（P04-06）。

关键设计（对齐架构 §11 依赖方向与 .clinerules 02）：
- ``create_celery_app`` 只有在显式调用时才配置 broker/backend（连接 Redis）；
  仅 import 本模块不会建立任何 Redis 连接。
- 默认情况下（不连真实 Redis）用内存传输跑 fake task 测试：
  - broker 用 ``memory://``（kombu 内存传输）
  - result backend 必须用 ``cache+memory://``（Celery 的内存缓存后端）
  单元测试默认使用 fake，不依赖真实 Redis。

依赖：infrastructure.queue -> application（端口）/ domain。
"""

from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]  # celery 无 mypy stub
from kombu import Queue  # type: ignore[import-untyped]  # kombu 无 mypy stub

__all__ = ["RESEARCH_QUEUE", "create_celery_app"]

RESEARCH_QUEUE = "research-jobs"


def create_celery_app(
    *,
    broker_url: str = "memory://",
    backend_url: str | None = None,
) -> Celery:
    """创建 Celery app。

    - ``broker_url`` 默认 ``memory://``（单元测试用，不连真实 Redis）；
      生产由调用方传入真实 Redis URL（如 ``redis://redis:6379/0``）。
    - ``backend_url`` 缺省为 ``cache+memory://``（测试用内存缓存后端）；
      两者都能被替换为真实 Redis。
    - task_acks_late=False + 单队列面向 MVP：worker 消费一次、失败由重试策略兜底。
    """
    resolved_backend = backend_url or "cache+memory://"
    app = Celery("invest_research")
    app.conf.broker_url = broker_url
    app.conf.result_backend = resolved_backend
    app.conf.task_default_queue = RESEARCH_QUEUE
    app.conf.task_queues = (Queue(RESEARCH_QUEUE),)
    # 显式关闭 pickle（安全）；仅允许 JSON 序列化。
    app.conf.task_serializer = "json"
    app.conf.result_serializer = "json"
    app.conf.accept_content = ("json",)
    # 连接/读取显式超时，避免卡死。
    app.conf.broker_transport_options = {"max_retries": 1, "interval_start": 0.1}
    return app
