"""P01-09 migration 002 集成测试：约束与索引。

upgrade head 后：新表存在、唯一/复合约束建对、索引存在；downgrade 到 0001 后新表消失。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()


def _alembic_cfg(url: str) -> Config:
    from pathlib import Path

    url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
    project_root = Path(os.path.abspath(__file__)).parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _tables(url: str) -> set[str]:
    url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
    engine = create_engine(url)
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def _indexes(url: str, table: str) -> set[str]:
    url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
    engine = create_engine(url)
    with engine.connect() as conn:
        insp = inspect(conn)
        return {name for ix in insp.get_indexes(table) if (name := ix["name"])}


def test_upgrade_creates_source_filing_document_tables(pg_container: str) -> None:
    """upgrade head 后 5 张新表存在且含索引与复合约束。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")

    tables = _tables(pg_container)
    assert {"sources", "job_sources", "filings", "documents", "document_chunks"} <= tables

    # 唯一约束：全部在 with 连接块内取值（with 结束后连接已关闭，再访问 insp 会报错）
    engine = create_engine(pg_container.replace("postgresql+psycopg2://", "postgresql+psycopg://"))
    with engine.connect() as conn:
        insp = inspect(conn)
        src_cols = [u["column_names"] for u in insp.get_unique_constraints("sources")]
        fil_cols = [
            c for u in insp.get_unique_constraints("filings") for c in (u["column_names"] or [])
        ]

    assert any(cols == ["canonical_url"] for cols in src_cols)
    assert "accession_number" in fil_cols

    # 索引
    assert "ix_filings_company_form_period" in _indexes(pg_container, "filings")
    assert "ix_sources_publisher_published" in _indexes(pg_container, "sources")


def test_downgrade_removes_new_tables(pg_container: str) -> None:
    """downgrade 到 0001 后 5 张新表消失，旧 3 表保留。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")

    tables = _tables(pg_container)
    for t in ("sources", "job_sources", "filings", "documents", "document_chunks"):
        assert t not in tables
    assert {"companies", "research_jobs", "workflow_steps"} <= tables
