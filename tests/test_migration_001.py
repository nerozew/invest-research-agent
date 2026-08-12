"""P01-08 migration 001 集成测试：upgrade / downgrade / upgrade 均可逆。

需要 Docker（Testcontainers 拉 PostgreSQL）。无 Docker 时 skip。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text


def _normalize_psycopg(url: str) -> str:
    """Testcontainers 默认返回 psycopg2 驱动前缀；本项目用 psycopg3，需规范化。"""
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


def _docker_available() -> bool:
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
    """启动 PostgreSQL 测试容器并返回连接 URL。"""
    # testcontainers 社区入口（官方主包已弃用 testcontainers.postgres）
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()


def _alembic_cfg(url: str) -> Config:
    from pathlib import Path

    url = _normalize_psycopg(url)

    project_root = Path(os.path.abspath(__file__)).parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _tables(url: str) -> set[str]:
    url = _normalize_psycopg(url)
    from sqlalchemy import create_engine

    engine = create_engine(url)
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def test_upgrade_downgrade_upgrade(pg_container: str) -> None:
    """upgrade head → 三表存在；downgrade base → 表消失；再 upgrade → 表回来。"""
    cfg = _alembic_cfg(pg_container)

    # 1) upgrade head
    command.upgrade(cfg, "head")
    tables = _tables(pg_container)
    assert {"companies", "research_jobs", "workflow_steps"} <= tables

    # 2) downgrade base
    command.downgrade(cfg, "base")
    tables = _tables(pg_container)
    assert "workflow_steps" not in tables
    assert "research_jobs" not in tables
    assert "companies" not in tables

    # 3) upgrade head 再跑一次
    command.upgrade(cfg, "head")
    tables = _tables(pg_container)
    assert {"companies", "research_jobs", "workflow_steps"} <= tables


def test_migration_version_table(pg_container: str) -> None:
    """Alembic version 表存在且记录 0001。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(_normalize_psycopg(pg_container))
    with engine.connect() as conn:
        version_num = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version_num == "0001"
