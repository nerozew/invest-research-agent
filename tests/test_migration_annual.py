"""P07 年度迁移：真实 PostgreSQL、独占临时库，不使用 create_all 替代迁移。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import db_integration_enabled
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from invest_research.domain.annual_pipeline import NodeDependency, ResearchNode, ResearchNodeKind
from invest_research.infrastructure.db.annual_node_store import SqlAnnualNodeStore
from invest_research.infrastructure.db.models import (
    AnnualNodeDependency,
    AnnualNodeEvent,
    AnnualResearchNode,
    ResearchJob,
)

pytestmark = pytest.mark.skipif(
    not db_integration_enabled(), reason="需要 Docker 或显式 TEST_DATABASE_URL"
)

ANNUAL_TABLES = {"annual_research_nodes", "annual_node_dependencies", "annual_node_events"}


@pytest.fixture
def migration_env(
    isolated_pg_url: str, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Config, Engine]]:
    monkeypatch.setenv("DATABASE_URL", isolated_pg_url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", isolated_pg_url)
    config.config_file_name = None
    engine = create_engine(isolated_pg_url)
    try:
        yield config, engine
    finally:
        engine.dispose()


def test_annual_upgrade_backfills_legacy_and_roundtrip_preserves_old_jobs(
    migration_env: tuple[Config, Engine],
) -> None:
    config, engine = migration_env
    command.upgrade(config, "0008")
    job_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO research_jobs "
                "(id, input_company, as_of_date, language, "
                "requested_forms, status, config_snapshot) "
                "VALUES (:id, 'Legacy fixture', '2025-10-31', 'zh-CN', "
                "'[\"10-K\"]', 'pending', '{}')"
            ),
            {"id": job_id},
        )

    command.upgrade(config, "head")
    assert ANNUAL_TABLES <= set(inspect(engine).get_table_names())
    columns = {column["name"]: column for column in inspect(engine).get_columns("research_jobs")}
    assert columns["research_mode"]["nullable"] is False
    assert "legacy" in columns["research_mode"]["default"]
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT research_mode FROM research_jobs WHERE id=:id"), {"id": job_id}
        ) == "legacy"

    command.downgrade(config, "0009")
    assert "research_mode" not in {
        column["name"] for column in inspect(engine).get_columns("research_jobs")
    }
    assert ANNUAL_TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(config, "0008")
    tables = set(inspect(engine).get_table_names())
    assert not ANNUAL_TABLES & tables
    assert {"research_jobs", "workflow_steps", "outbox_events"} <= tables
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT input_company, research_mode FROM research_jobs WHERE id=:id"),
            {"id": job_id},
        ).one()
    assert tuple(row) == ("Legacy fixture", "legacy")


def test_annual_migration_enforces_unique_constraints_and_timezone_columns(
    migration_env: tuple[Config, Engine],
) -> None:
    config, engine = migration_env
    command.upgrade(config, "head")
    expected_uniques = {
        "annual_research_nodes": ("job_id", "node_key"),
        "annual_node_dependencies": ("job_id", "upstream_node_id", "downstream_node_id"),
        "annual_node_events": ("node_id", "event_no"),
    }
    inspector = inspect(engine)
    for table, unique in expected_uniques.items():
        assert unique in {
            tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)
        }
        time_columns = [
            column for column in inspector.get_columns(table) if column["name"].endswith("_at")
        ]
        assert time_columns
        assert all(column["type"].timezone for column in time_columns)

    factory = sessionmaker(bind=engine, autoflush=False)
    job_id = uuid.uuid4()
    with factory() as session:
        session.add(ResearchJob(id=job_id, input_company="Acme", as_of_date=date(2025, 10, 31)))
        session.commit()
    SqlAnnualNodeStore(factory).create_graph(
        job_id=job_id,
        nodes=tuple(
            ResearchNode(node_key=key, kind=ResearchNodeKind.DOWNLOAD_FILING) for key in ("a", "b")
        ),
        dependencies=(NodeDependency(upstream_node_key="a", downstream_node_key="b"),),
    )
    for model in (AnnualResearchNode, AnnualNodeDependency, AnnualNodeEvent):
        with engine.connect() as connection:
            duplicate = dict(connection.execute(select(model.__table__).limit(1)).mappings().one())
        duplicate["id"] = uuid.uuid4()
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(model.__table__.insert().values(**duplicate))
