"""P01-10 migration 003 集成测试：Decimal 精度与 FK。

upgrade head 后：4 张新表存在、value 为 NUMERIC(38,10)、外键指向正确；
downgrade 到 0002 后 4 表消失，前 8 表保留。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Numeric, create_engine, inspect


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


def _norm(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


@pytest.fixture(scope="module")
def pg_container() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()


def _alembic_cfg(url: str) -> Config:
    from pathlib import Path

    project_root = Path(os.path.abspath(__file__)).parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _norm(url))
    return cfg


def _tables(url: str) -> set[str]:
    engine = create_engine(_norm(url))
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def _fks(url: str, table: str) -> set[str]:
    engine = create_engine(_norm(url))
    with engine.connect() as conn:
        insp = inspect(conn)
        # fk["referred_table"] 是单个字符串（目标表名），不是列表
        return {fk["referred_table"] for fk in insp.get_foreign_keys(table)}


def test_upgrade_creates_fact_metric_report_citation_tables(pg_container: str) -> None:
    """4 张新表存在；financial_facts.value 为 NUMERIC(38,10)；外键指向正确。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")

    tables = _tables(pg_container)
    assert {"financial_facts", "computed_metrics", "reports", "citations"} <= tables

    # Decimal 精度：financial_facts.value 列应 numeric(38,10)
    engine = create_engine(_norm(pg_container))
    with engine.connect() as conn:
        insp = inspect(conn)
        value_col = next(c for c in insp.get_columns("financial_facts") if c["name"] == "value")
    assert isinstance(value_col["type"], Numeric)
    assert value_col["type"].precision == 38
    assert value_col["type"].scale == 10

    # FK
    assert "companies" in _fks(pg_container, "financial_facts")
    assert "sources" in _fks(pg_container, "financial_facts")
    assert "filings" in _fks(pg_container, "financial_facts")
    assert "research_jobs" in _fks(pg_container, "computed_metrics")
    assert "research_jobs" in _fks(pg_container, "reports")
    assert "reports" in _fks(pg_container, "citations")
    assert "sources" in _fks(pg_container, "citations")


def test_downgrade_removes_new_tables(pg_container: str) -> None:
    """downgrade 到 0002 后 4 表消失，前 8 表保留。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")

    tables = _tables(pg_container)
    for t in ("financial_facts", "computed_metrics", "reports", "citations"):
        assert t not in tables
    assert {
        "companies",
        "research_jobs",
        "workflow_steps",
        "sources",
        "job_sources",
        "filings",
        "documents",
        "document_chunks",
    } <= tables
