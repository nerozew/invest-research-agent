"""P01-07 集成测试：建连 + 事务回滚。

需要 Docker（testcontainers 自动拉起 PostgreSQL 容器）才能真实运行。
本机无 Docker 时通过 skipif 跳过；验收条件满足需在 Docker 环境真实跑绿。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from invest_research.infrastructure.db.base import (
    create_db_engine,
    create_session_factory,
)


def _docker_available() -> bool:
    """探测 Docker 是否可用（避免 testcontainers 启动失败抛异常）。"""
    try:
        from docker import from_env  # type: ignore[import-untyped]

        client = from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available() or os.environ.get("SKIP_DB_TESTS") == "1",
    reason="需要运行时 Docker（testcontainers）",
)


@pytest.fixture(scope="module")
def pg_container() -> Iterator[str]:
    """启动 PostgreSQL 测试容器并返回其连接 URL。"""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()


def test_connect_and_rollback_transaction(pg_container: str) -> None:
    """能建连；事务内改动在回滚后不生效。"""
    engine = create_db_engine(pg_container)
    session_factory = create_session_factory(engine)

    with session_factory() as session, session.begin():
        session.execute(text("CREATE TABLE IF NOT EXISTS _probe (id serial PRIMARY KEY, v int)"))
        session.execute(text("INSERT INTO _probe (v) VALUES (1)"))

    with session_factory() as session:
        count = session.execute(text("SELECT COUNT(*) FROM _probe")).scalar_one()
        assert count == 1

    # 事务回滚：新事务里插入后回滚
    with session_factory() as session:
        session.begin()
        session.execute(text("INSERT INTO _probe (v) VALUES (2)"))
        session.rollback()

    with session_factory() as session:
        count = session.execute(text("SELECT COUNT(*) FROM _probe")).scalar_one()
        assert count == 1  # 回滚后只有 1 行


def test_session_works_within_transaction(pg_container: str) -> None:
    """session 可执行简单查询（建连健康）。"""
    engine = create_db_engine(pg_container)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        result = session.execute(text("SELECT 1")).scalar_one()
    assert result == 1
