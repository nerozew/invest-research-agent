"""P01-11 migration 004 集成测试：幂等唯一约束。

upgrade head 后：2 张新表存在、唯一约束建对；downgrade 到 0003 后 2 表消失、前 12 表保留。
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from conftest import db_integration_enabled
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.skipif(
    not db_integration_enabled(),
    reason="需要运行时 Docker（testcontainers）或 TEST_DATABASE_URL",
)


def _norm(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


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


def _uniques(url: str, table: str) -> list[list[str]]:
    engine = create_engine(_norm(url))
    with engine.connect() as conn:
        return [u["column_names"] for u in inspect(conn).get_unique_constraints(table)]


def test_upgrade_creates_invocation_artifact_tables(pg_container: str) -> None:
    """2 张新表存在；唯一约束列为预期集合。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")

    tables = _tables(pg_container)
    assert {"tool_invocations", "artifacts"} <= tables

    inv_uniques = _uniques(pg_container, "tool_invocations")
    art_uniques = _uniques(pg_container, "artifacts")
    assert any(
        cols == ["job_id", "tool_name", "invocation_key", "attempt_no"] for cols in inv_uniques
    )
    assert any(cols == ["job_id", "artifact_key"] for cols in art_uniques)


def test_idempotency_unique_rejects_duplicate(pg_container: str) -> None:
    """同一幂等键的重复写入被数据库唯一约束拒绝。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")

    engine = create_engine(_norm(pg_container))
    from uuid import uuid4

    job_id = str(uuid4())
    # 先插入一个 job 以满足 FK
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO research_jobs (id, input_company, as_of_date, language, "
                "requested_forms, status, config_snapshot) "
                "VALUES (:id, 'TEST', CURRENT_DATE, 'zh-CN', '[]', 'pending', '{}')"
            ),
            {"id": job_id},
        )
        conn.execute(
            text(
                "INSERT INTO artifacts (id, job_id, artifact_key, artifact_type, "
                "storage_uri, content_checksum, byte_size) "
                "VALUES (:id, :job_id, 'k1', 'json', 'u1', 'c1', 10)"
            ),
            {"id": str(uuid4()), "job_id": job_id},
        )
        conn.commit()

    # 同 job_id + artifact_key 再插 → 应被唯一约束拒绝。
    # SQLAlchemy 会把底层 psycopg UniqueViolation 包装为 sqlalchemy.exc.IntegrityError。
    from sqlalchemy import exc

    with pytest.raises(exc.IntegrityError):
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO artifacts (id, job_id, artifact_key, artifact_type, "
                    "storage_uri, content_checksum, byte_size) "
                    "VALUES (:id, :job_id, 'k1', 'json', 'u2', 'c2', 20)"
                ),
                {"id": str(uuid4()), "job_id": job_id},
            )
            conn.commit()


def test_downgrade_removes_new_tables(pg_container: str) -> None:
    """downgrade 到 0003 后 2 表消失、前 12 表保留。"""
    cfg = _alembic_cfg(pg_container)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003")

    tables = _tables(pg_container)
    assert "tool_invocations" not in tables
    assert "artifacts" not in tables
    assert {
        "companies",
        "research_jobs",
        "workflow_steps",
        "sources",
        "job_sources",
        "filings",
        "documents",
        "document_chunks",
        "financial_facts",
        "computed_metrics",
        "reports",
        "citations",
    } <= tables
