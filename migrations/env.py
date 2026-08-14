"""Alembic environment：离线与集成迁移共用。"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让项目包可导入（migrations/ 在项目根下，根在 sys.path）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import invest_research.infrastructure.db.models  # noqa: E402,F401  # 注册所有表
from invest_research.infrastructure.db.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 12-factor：优先使用 DATABASE_URL 环境变量（Docker Compose 传入服务名），
# 未设置时回退到 alembic.ini 的 sqlalchemy.url。
_database_url = config.get_main_option("sqlalchemy.url")
if "DATABASE_URL" in config.attributes or __import__("os").environ.get("DATABASE_URL"):
    import os

    _database_url = os.environ.get("DATABASE_URL", _database_url)
    config.set_main_option("sqlalchemy.url", _database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
