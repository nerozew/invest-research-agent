"""0003 fact / metric / report / citation 系列表

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("filing_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy", sa.String(100), nullable=False),
        sa.Column("concept", sa.String(300), nullable=False),
        sa.Column("label", sa.String(500), nullable=True),
        sa.Column("value", sa.Numeric(38, 10), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("instant_date", sa.Date(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(20), nullable=True),
        sa.Column("form_type", sa.String(20), nullable=True),
        sa.Column("frame", sa.String(50), nullable=True),
        sa.Column("accession_number", sa.String(40), nullable=True),
        sa.Column("fact_version", sa.String(20), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "source_id",
            "taxonomy",
            "concept",
            "unit",
            "period_start",
            "period_end",
            "accession_number",
            "fact_version",
            name="uq_financial_facts_identity",
        ),
    )
    op.create_index(
        "ix_financial_facts_lookup",
        "financial_facts",
        ["company_id", "concept", "period_end", "form_type"],
    )

    op.create_table(
        "computed_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(38, 10), nullable=True),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("formula_version", sa.String(50), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "metric_name",
            "period_end",
            "formula_version",
            name="uq_computed_metrics_identity",
        ),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("markdown_uri", sa.String(2048), nullable=True),
        sa.Column("pdf_uri", sa.String(2048), nullable=True),
        sa.Column("content_checksum", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("quality_summary", sa.JSON(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "version", name="uq_reports_job_version"),
    )

    op.create_table(
        "citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("claim_key", sa.String(200), nullable=False),
        sa.Column("section_key", sa.String(100), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("locator", sa.String(500), nullable=True),
        sa.Column("supports_claim", sa.Boolean(), nullable=True),
        sa.Column("validation_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id",
            "claim_key",
            "source_id",
            "locator",
            name="uq_citations_identity",
        ),
    )
    op.create_index("ix_citations_report_section", "citations", ["report_id", "section_key"])


def downgrade() -> None:
    # 先删索引再删表；删表会连带删除该表上的索引
    op.drop_index("ix_citations_report_section", table_name="citations")
    op.drop_index("ix_financial_facts_lookup", table_name="financial_facts")
    op.drop_table("citations")
    op.drop_table("reports")
    op.drop_table("computed_metrics")
    op.drop_table("financial_facts")
