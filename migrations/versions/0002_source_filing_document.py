"""0002 source / filing / document 系列表

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_checksum", sa.String(128), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_url"),
    )
    op.create_index("ix_sources_publisher_published", "sources", ["publisher", "published_at"])

    op.create_table(
        "job_sources",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id", "source_id", "purpose", name="pk_job_sources"),
    )

    op.create_table(
        "filings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("accession_number", sa.String(40), nullable=False),
        sa.Column("form_type", sa.String(20), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("report_period", sa.Date(), nullable=True),
        sa.Column("primary_document_url", sa.String(2048), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("is_amendment", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accession_number"),
    )
    op.create_index(
        "ix_filings_company_form_period", "filings", ["company_id", "form_type", "report_period"]
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("filing_id", sa.Uuid(), nullable=True),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("storage_uri", sa.String(2048), nullable=False),
        sa.Column("content_checksum", sa.String(128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("parse_status", sa.String(20), nullable=False),
        sa.Column("parser_name", sa.String(100), nullable=True),
        sa.Column("parser_version", sa.String(50), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_checksum", "parser_name", "parser_version", name="uq_documents_checksum_parser"
        ),
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.String(500), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_doc_index"),
    )


def downgrade() -> None:
    # 重要：先删索引再删表。drop table 会连同索引一起删掉，
    # 若先 drop table 再 drop_index 会报 "index does not exist"。
    op.drop_index("ix_filings_company_form_period", table_name="filings")
    op.drop_index("ix_sources_publisher_published", table_name="sources")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("filings")
    op.drop_table("job_sources")
    op.drop_table("sources")
