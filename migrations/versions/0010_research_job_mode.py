"""P07-10A persist research mode for worker-side runtime dispatch.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    context = op.get_context()
    column = sa.Column("research_mode", sa.String(20), nullable=False, server_default="legacy")
    if context.bind.dialect.name == "postgresql":
        op.add_column("research_jobs", column)
    else:
        with op.batch_alter_table("research_jobs") as batch_op:
            batch_op.add_column(column)


def downgrade() -> None:
    context = op.get_context()
    if context.bind.dialect.name == "postgresql":
        op.drop_column("research_jobs", "research_mode")
    else:
        with op.batch_alter_table("research_jobs") as batch_op:
            batch_op.drop_column("research_mode")
