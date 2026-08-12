"""0004 invocation / artifact 系列表

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_step_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("invocation_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("request_redacted", sa.JSON(), nullable=False),
        sa.Column("response_summary", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_step_id"], ["workflow_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "tool_name",
            "invocation_key",
            "attempt_no",
            name="uq_tool_invocations_power_idempotent",
        ),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_step_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_key", sa.String(200), nullable=False),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=True),
        sa.Column("storage_uri", sa.String(2048), nullable=False),
        sa.Column("content_checksum", sa.String(128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_step_id"], ["workflow_steps.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "artifact_key", name="uq_artifacts_job_key"),
    )


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("tool_invocations")
