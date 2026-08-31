"""0008 research_jobs 增加 failure_stage 列（P06-09 失败阶段收口）

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    context = op.get_context()
    # SQLite（离线测试）不支持 ADD COLUMN IF NOT EXISTS 之外的复杂选项；
    # PostgreSQL/SQLite 都支持 nullable 列直接 ADD COLUMN。
    if context.bind.dialect.name == "postgresql":
        op.add_column(
            "research_jobs",
            sa.Column("failure_stage", sa.String(50), nullable=True),
        )
    else:
        with op.batch_alter_table("research_jobs") as batch_op:
            batch_op.add_column(sa.Column("failure_stage", sa.String(50), nullable=True))


def downgrade() -> None:
    context = op.get_context()
    if context.bind.dialect.name == "postgresql":
        op.drop_column("research_jobs", "failure_stage")
    else:
        with op.batch_alter_table("research_jobs") as batch_op:
            batch_op.drop_column("failure_stage")
