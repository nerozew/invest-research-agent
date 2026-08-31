"""P07-10 年度 DAG 节点持久化与追加式事件账本。

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annual_research_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=200), nullable=False),
        sa.Column("node_kind", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("output_artifact_keys", sa.JSON(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "node_key", name="uq_annual_research_nodes_job_key"),
    )
    op.create_index("ix_annual_research_nodes_job_id", "annual_research_nodes", ["job_id"])
    op.create_table(
        "annual_node_dependencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("upstream_node_id", sa.Uuid(), nullable=False),
        sa.Column("downstream_node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["upstream_node_id"], ["annual_research_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["downstream_node_id"], ["annual_research_nodes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "upstream_node_id",
            "downstream_node_id",
            name="uq_annual_node_dependencies_edge",
        ),
    )
    op.create_index("ix_annual_node_dependencies_job_id", "annual_node_dependencies", ["job_id"])
    op.create_table(
        "annual_node_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("event_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("previous_status", sa.String(length=30), nullable=True),
        sa.Column("new_status", sa.String(length=30), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["annual_research_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "event_no", name="uq_annual_node_events_node_sequence"),
    )
    op.create_index("ix_annual_node_events_job_id", "annual_node_events", ["job_id"])
    op.create_index("ix_annual_node_events_node_id", "annual_node_events", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_annual_node_events_node_id", table_name="annual_node_events")
    op.drop_index("ix_annual_node_events_job_id", table_name="annual_node_events")
    op.drop_table("annual_node_events")
    op.drop_index("ix_annual_node_dependencies_job_id", table_name="annual_node_dependencies")
    op.drop_table("annual_node_dependencies")
    op.drop_index("ix_annual_research_nodes_job_id", table_name="annual_research_nodes")
    op.drop_table("annual_research_nodes")
