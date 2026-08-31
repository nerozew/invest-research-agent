"""消息队列基础设施（P04-06）。

依赖方向：infrastructure -> application/domain。

约定：
- **模块导入不连接 Redis**：Celery app 的 broker 在 ``create_celery_app``
  被显式调用时才配置；仅 import 本模块不会建立任何 Redis 连接。
- worker 消费的 task 在 ``tasks.py`` 注册；P04-07 将把 fake task 替换为
  调用 Flow 的 adapter。
"""
