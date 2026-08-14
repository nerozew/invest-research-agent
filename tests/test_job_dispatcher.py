"""P04-10A：Celery 投递实现测试。

用 memory broker app 验证 send_task 能成功投递（不连真实 Redis）。
"""

from __future__ import annotations

import uuid

from invest_research.infrastructure.queue.celery_app import create_celery_app
from invest_research.infrastructure.queue.job_dispatcher import CeleryJobDispatcher


def test_celery_dispatcher_does_not_raise():
    app = create_celery_app(broker_url="memory://", backend_url="cache+memory://")
    dispatcher = CeleryJobDispatcher(app)
    job_id = uuid.uuid4()
    # memory broker 不阻塞；调用不应抛异常
    dispatcher.dispatch(job_id)
