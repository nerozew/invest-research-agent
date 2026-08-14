"""P04-06：Redis 与 Celery 最小 worker 测试。

- 模块导入不连接 Redis（进程内不会有真实 broker 连接）。
- 用 memory:// + task_always_eager 验证"投递 → worker 消费 fake task"链路。
- fake handler 记录被消费的 job_id，断言 handler.process 被调用。
"""

from __future__ import annotations

import uuid

from invest_research.infrastructure.queue.celery_app import RESEARCH_QUEUE, create_celery_app
from invest_research.infrastructure.queue.tasks import (
    TASK_PROCESS_JOB,
    register_tasks,
)


class FakeHandler:
    """fake job handler：记录被处理的 job_id（对齐'测试默认使用 fake'）。"""

    def __init__(self) -> None:
        self.processed: list[uuid.UUID] = []

    def process(self, job_id: uuid.UUID) -> None:
        self.processed.append(job_id)


def test_import_does_not_connect_redis() -> None:
    """仅 import（不调用 create_celery_app）不应创建真实 broker 连接。"""
    import importlib

    # 重新导入并确认无副作用（不触发 Redis 连接）
    importlib.reload(
        importlib.import_module("invest_research.infrastructure.queue.celery_app")
    )
    # 断言：创建 app 默认 memory://（不连真实 Redis）
    app = create_celery_app()
    assert app.conf.broker_url == "memory://"
    assert app.conf.task_default_queue == RESEARCH_QUEUE


def test_dispatch_and_consume_fake_task() -> None:
    """API 投递 → worker 消费 fake task：job_id 被 fake handler 处理。"""
    app = create_celery_app()
    # eager 模式：apply_async 立即由"worker"同步消费（不依赖真实 Redis）
    app.conf.task_always_eager = True
    handler = FakeHandler()
    register_tasks(app, handler)

    job_id = uuid.uuid4()
    task = app.tasks[TASK_PROCESS_JOB]
    result = task.apply_async(kwargs={"job_id": str(job_id)})

    # worker 消费返回 job_id
    assert result.get() == str(job_id)
    # fake handler 确实处理了该 job
    assert handler.processed == [job_id]


def test_queue_config_and_json_serializer() -> None:
    """队列名与 JSON 序列化配置。"""
    app = create_celery_app()
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ("json",)
    # pickle 被禁用（安全）
    assert "pickle" not in app.conf.accept_content
