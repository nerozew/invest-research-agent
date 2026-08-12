"""SQLAlchemy Base 与 engine 工厂（P01-07）。

- DeclarativeBase：所有 ORM 映射（P01-08 起）都继承它；
- create_engine / create_session_factory：统一的 connection/session 工厂；
- 显式连接超时，支持 PostgreSQL（psycopg3）驱动。

依赖边界：infrastructure 允许导入 SQLAlchemy（外部技术适配层），
但 domain 层禁止导入本模块。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """所有 ORM 映射模型的基类。"""


def create_db_engine(database_url: str) -> Engine:
    """按 URL 创建 engine。

    - pool_pre_ping=True：取连接前先 ping，避免陈旧连接。
    - connect_args 显式超时由驱动/URL 控制（psycopg 支持 connect_timeout）。
    """
    return create_engine(database_url, pool_pre_ping=True, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """基于 engine 创建 session 工厂。"""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
