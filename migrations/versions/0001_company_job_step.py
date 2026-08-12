"""0001 company / job / step 三表

Revision ID: 0001
Revises:
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # companies
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("legal_name", sa.String(300), nullable=False),
        sa.Column("exchange", sa.String(50), nullable=True),
        sa.Column("sic", sa.String(10), nullable=True),
        sa.Column("fiscal_year_end", sa.String(4), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cik"),
    )
    # research_jobs
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("input_company", sa.String(200), nullable=False),
        sa.Column("input_ticker", sa.String(20), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("requested_forms", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_step", sa.String(50), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    # workflow_steps
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("step_name", sa.String(50), nullable=False),
        sa.Column("sequence_no", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("output_schema_version", sa.String(50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "step_name", name="uq_workflow_steps_job_step"),
        sa.UniqueConstraint("job_id", "sequence_no", name="uq_workflow_steps_job_seq"),
    )


def downgrade() -> None:
    op.drop_table("workflow_steps")
    op.drop_table("research_jobs")
    op.drop_table("companies")
