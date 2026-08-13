"""Health/Readiness 检查器（P04-01）。

职责划分：
- ``HealthChecker``：依赖注入的接口（Protocol）。FastAPI factory 通过构造参数接收
  checker 实例，路由只依赖该接口，不耦合 SQLAlchemy/Redis 具体实现。
- ``DependencyHealthChecker``：默认实现，分别探测 PostgreSQL 与 Redis。
  探测失败时只记录分类后的错误码，**绝不**返回连接字符串、密码或 URL。
- ``build_health_checker``：根据 Settings 创建带显式超时的引擎/Redis 客户端并组装
  默认实现，供 application factory 使用。

说明：探测器为同步方法。FastAPI 路由以 ``def``（非 ``async def``）定义时，
FastAPI 会自动把调用调度到线程池，避免阻塞事件循环，因此保持同步实现最简。

依赖方向：api -> infrastructure。
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, TypedDict

from pydantic import BaseModel
from redis import Redis
from sqlalchemy import Engine, text

from invest_research.settings import Settings

logger = logging.getLogger(__name__)

# 依赖名称常量：作为响应 JSON 的稳定键，同时是类型层面的封闭集合。
DependencyName = Literal["database", "redis"]

# ``_UnhealthyReason`` 只承载分类后的错误码（如 ``connect_timeout``），
# 禁止把异常字符串、URL、密码等敏感信息放入其中。
_UnhealthyReason = Literal["connect_timeout", "connection_error", "unknown"]

# 各依赖探测结果（结构化结果，不携带敏感信息）。
_CheckResult = TypedDict(
    "_CheckResult",
    {
        "status": Literal["ok", "unavailable"],
        "error_code": _UnhealthyReason | None,
    },
)


class HealthResponse(BaseModel):
    """``/health`` 响应：只表示 API 进程存活，不访问任何外部依赖。"""

    status: Literal["ok"] = "ok"
    service: str


class DependencyStatus(BaseModel):
    """单个依赖的就绪状态。``error_code`` 为分类后的错误码，绝不含敏感信息。"""

    status: Literal["ok", "unavailable"]
    error_code: _UnhealthyReason | None = None


class ReadinessResponse(BaseModel):
    """``/readiness`` 响应：分开报告 database 与 redis 两个依赖就绪状态。"""

    status: Literal["ready", "not_ready"]
    ready: bool
    database: DependencyStatus
    redis: DependencyStatus


class HealthChecker(Protocol):
    """readiness 依赖探测的接口。

    实现者负责探测单一依赖并返回分类后的结果；调用方（路由）负责把结果
    转成 HTTP 语义（200/503）。
    """

    def check_database(self) -> _CheckResult: ...
    def check_redis(self) -> _CheckResult: ...


class DependencyHealthChecker:
    """基于真实 PostgreSQL/Redis 的默认实现。

    引擎与 Redis 客户端在创建时已由 ``build_health_checker`` 配置显式超时
    （见 Settings.readiness_*_connect_timeout）。探测失败被捕获并归类为错误码，
    不会把底层异常（可能含 URL/密码）传播给路由。
    """

    def __init__(self, db_engine: Engine, redis_client: Redis) -> None:
        self._db_engine = db_engine
        self._redis_client = redis_client

    def check_database(self) -> _CheckResult:
        try:
            with self._db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"status": "ok", "error_code": None}
        except TimeoutError:
            _log_unhealthy("database", "connect_timeout")
            return {"status": "unavailable", "error_code": "connect_timeout"}
        except OSError:
            _log_unhealthy("database", "connection_error")
            return {"status": "unavailable", "error_code": "connection_error"}
        except Exception:
            logger.exception("database readiness probe failed with unexpected error")
            return {"status": "unavailable", "error_code": "unknown"}

    def check_redis(self) -> _CheckResult:
        try:
            pong = self._redis_client.ping()
            if pong is not True:
                return {"status": "unavailable", "error_code": "connection_error"}
            return {"status": "ok", "error_code": None}
        except TimeoutError:
            _log_unhealthy("redis", "connect_timeout")
            return {"status": "unavailable", "error_code": "connect_timeout"}
        except OSError:
            _log_unhealthy("redis", "connection_error")
            return {"status": "unavailable", "error_code": "connection_error"}
        except Exception:
            logger.exception("redis readiness probe failed with unexpected error")
            return {"status": "unavailable", "error_code": "unknown"}


def _log_unhealthy(dependency: DependencyName, reason: _UnhealthyReason) -> None:
    """记录分类后的探测失败（不含连接串/密码）。"""
    logger.warning("%s readiness probe failed: error_code=%s", dependency, reason)


def build_health_checker(settings: Settings) -> DependencyHealthChecker:
    """根据 Settings 创建带显式超时的探测资源并组装默认 checker。

    只有显式调用本函数（即 application factory 未注入 checker 时）才会创建
    PostgreSQL engine 与 Redis 客户端，且超时来自 Settings：
    - database：connect_args["connect_timeout"] 与 pool_timeout
    - redis   ：socket_connect_timeout 与 socket_timeout

    engine/redis 的关闭由 application factory 的 lifespan 负责。
    """
    from sqlalchemy import create_engine

    db_engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        pool_timeout=settings.readiness_db_connect_timeout,
        connect_args={"connect_timeout": int(settings.readiness_db_connect_timeout)},
    )
    redis_client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.readiness_redis_connect_timeout,
        socket_timeout=settings.readiness_redis_connect_timeout,
        decode_responses=True,
    )
    return DependencyHealthChecker(db_engine, redis_client)


def dispose_dependency_resources(checker: DependencyHealthChecker) -> None:
    """释放默认 checker 持有的探测资源（engine/redis 连接池）。"""
    checker._db_engine.dispose()
    checker._redis_client.close()


# 供外部（含 mypy 测试断言）引用的公开 API。
__all__ = [
    "DependencyHealthChecker",
    "DependencyStatus",
    "HealthChecker",
    "HealthResponse",
    "ReadinessResponse",
    "build_health_checker",
    "dispose_dependency_resources",
]
