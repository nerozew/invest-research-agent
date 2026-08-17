"""P01-08 migration 001 集成测试：upgrade / downgrade / upgrade 均可逆。

需要 Docker（Testcontainers 拉 PostgreSQL）。无 Docker 时 skip。
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from conftest import db_integration_enabled
from sqlalchemy import inspect, text

pytestmark = pytest.mark.skipif(
    not db_integration_enabled(),
    reason="需要运行时 Docker（testcontainers）或 TEST_DATABASE_URL",
)


def _normalize_psycopg(url: str) -> str:
    """Testcontainers 默认返回 psycopg2 驱动前缀；本项目用 psycopg3，需规范化。"""
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


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
    """Alembic version 表存在且记录当前 head（0008）。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(_normalize_psycopg(pg_container))
    with engine.connect() as conn:
        version_num = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version_num == "0008"
