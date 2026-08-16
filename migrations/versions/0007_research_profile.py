"""0007 research_jobs.research_profile（P06-06A 每任务档位）

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 research_jobs 添加 research_profile 列，旧数据回填为 deep（默认档位）。"""
    op.add_column(
        "research_jobs",
        sa.Column("research_profile", sa.String(10), nullable=False, server_default="deep"),
    )


def downgrade() -> None:
    """移除 research_profile 列（可回滚）。"""
    op.drop_column("research_jobs", "research_profile")