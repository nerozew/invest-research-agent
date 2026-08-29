"""pytest 全局环境隔离：清空会污染 Settings 默认值的环境变量。

背景：CrewAI 导入时会 load_dotenv() 把项目根 .env 灌进 os.environ；即使测试用
`_env_file=None`，pydantic-settings 仍会读到这些已注入 os.environ 的变量，
导致「断言默认值」的测试随真实 .env 内容时好时坏（例如真实 .env 里
LLM_MODEL_RESEARCH=qwen3.5-flash、LLM_BASE_URL 指向专属 workspace）。

这里在每次测试前清空相关变量；测试内部仍可用 `monkeypatch.setenv` 重新覆盖。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import appdirs
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.infrastructure.db.base import Base

# 会影响 Settings 默认值/必需字段的环境变量（真实 .env 可能注入）
_POLLUTING_ENV: tuple[str, ...] = (
    "LLM_PROVIDER",
    "LLM_VENDOR",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL_RESEARCH",
    "LLM_MODEL_ANALYSIS",
    "LLM_MODEL_WRITER",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT",
    "LLM_ENABLE_THINKING",
    "LLM_RESEARCH_ENABLE_THINKING",
    "LLM_ANALYSIS_ENABLE_THINKING",
    "LLM_WRITER_ENABLE_THINKING",
    "SEC_USER_AGENT_CONTACT",
    "SERPER_API_KEY",
    "SERPER_ENDPOINT",
    "FLOW_MODE",
    "RESEARCH_PROFILE",
    "DIAGNOSTIC_CAPTURE_MODE",
    "PROJECT_NAME",
    "ENVIRONMENT",
    "LOG_LEVEL",
)


def _redirect_crewai_user_data_dir(
    appname: str | None,
    appauthor: str | None = None,
    version: str | None = None,
    roaming: bool = False,
) -> str:
    """把 CrewAI 的 user-data 目录重定向到本次会话唯一的临时目录。

    CrewAI 会把 SQLite 任务输出存储写到 ``appdirs.user_data_dir(...)``
    （Windows 上为真实 LOCALAPPDATA\\CrewAI\\<项目名>）。受限文件沙箱下，
    该真实目录属于"外源只读"，写入会报 readonly database / PermissionError；
    同时测试也不应污染用户的真实应用数据。这里重定向到系统临时目录下
    的会话唯一目录（沙箱可写、且不跨会话复用）。
    """
    root = Path(tempfile.gettempdir()) / f"crewai-data-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _docker_available() -> bool:
    """探测 Docker 是否可用（避免 testcontainers 启动失败抛异常）。"""
    try:
        from docker import from_env  # type: ignore[import-untyped]

        client = from_env()
        client.ping()
        return True
    except Exception:
        return False


def db_integration_enabled() -> bool:
    """DB 集成测试是否应实际运行。

    - ``SKIP_DB_TESTS=1``：显式跳过（离线单测 job 使用）；
    - 存在 ``TEST_DATABASE_URL``：GitHub Actions service PostgreSQL，直接运行；
    - 否则：需要本地 Docker（testcontainers），无 Docker 则跳过。
    """
    if os.environ.get("SKIP_DB_TESTS") == "1":
        return False
    if os.environ.get("TEST_DATABASE_URL"):
        return True
    return _docker_available()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[str]:
    """DB 集成测试共享的 PostgreSQL 连接 URL。

    优先使用 GitHub Actions CI 注入的 ``TEST_DATABASE_URL``（service 容器，
    无需 Docker）；本地无该变量时 fallback 到 testcontainers 自动拉起
    ``postgres:16-alpine`` 容器。测试文件只依赖原始 schema URL，由各自的
    ``_norm`` 统一把 ``psycopg2`` 驱动前缀规范为 ``psycopg``。
    """
    external_url = os.environ.get("TEST_DATABASE_URL")
    if external_url:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv(
                "DATABASE_URL", external_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
            )
            yield external_url
        return

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        url = postgres.get_connection_url()
        # Alembic env 优先读 DATABASE_URL；旧迁移测试也必须指向此临时容器，
        # 不能被开发环境 .env 或 CI 的 SQLite 默认值覆盖。
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", url.replace("postgresql+psycopg2://", "postgresql+psycopg://"))
            yield url


@pytest.fixture
def isolated_pg_url(pg_container: str) -> Iterator[str]:
    """每个用例独占新库；只删除本 fixture 在测试服务器上创建的随机库。"""
    admin_url = make_url(pg_container).set(drivername="postgresql+psycopg")
    database_name = f"p07_test_{uuid.uuid4().hex}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        try:
            yield admin_url.set(database=database_name).render_as_string(hide_password=False)
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
    finally:
        admin.dispose()


@pytest.fixture
def pg_session_factory(
    isolated_pg_url: str, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[sessionmaker[Session]]:
    """使用真实迁移和 Worker 的事务配置；禁止环境变量把迁移导向其他库。"""
    monkeypatch.setenv("DATABASE_URL", isolated_pg_url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", isolated_pg_url)
    # 不让 Alembic 的 fileConfig 禁用 pytest 正在捕获的应用日志。
    config.config_file_name = None
    command.upgrade(config, "head")
    engine = create_engine(isolated_pg_url)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)
    finally:
        engine.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
def sql_session_factory(request: pytest.FixtureRequest) -> Iterator[sessionmaker[Session]]:
    """同一份状态/接口回归同时覆盖 SQLite 与经迁移的 PostgreSQL。"""
    if request.param == "postgresql":
        if not db_integration_enabled():
            pytest.skip("PostgreSQL 集成测试未启用")
        yield request.getfixturevalue("pg_session_factory")
        return
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)
    finally:
        engine.dispose()


# 在 conftest 加载期替换 appdirs.user_data_dir：CrewAI 通过模块引用调用，
# 因此对后续所有导入 CrewAI 的测试都生效。
appdirs.user_data_dir = _redirect_crewai_user_data_dir  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _clear_polluting_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次测试前清空会污染 Settings 的环境变量（测试结束由 monkeypatch 自动恢复）。"""
    for name in _POLLUTING_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def tmp_path_factory() -> Any:
    """沙箱安全的临时目录工厂（覆盖 pytest 内置 fixture）。

    背景：pytest 内置 tmp 机制用 ``mkdir(mode=0o700)`` 创建临时目录；受限文件
    沙箱会把 0o700 目录视为"私有/外源"而拒绝枚举/写入（WinError 5），导致所有
    ``tmp_path`` 测试报 PermissionError。这里用默认权限（0o777 & umask）创建，
    根目录每次会话唯一（避免跨命令删除/复用被沙箱拒绝），且不主动删除（交给
    系统清理），使测试可在受限文件沙箱下正常运行。

    兼容 pytest.TempPathFactory 测试所需的最小接口：getbasetemp / mktemp。
    """
    root = Path(tempfile.gettempdir()) / f"invest-research-tmp-{uuid.uuid4().hex}"
    root.mkdir()

    class SandboxSafeTempPathFactory:
        # 兼容 pytest 内置 tmp_path/tmp_path_factory 用到的内部属性：
        # _retention_policy="all" 表示不删除临时目录（沙箱下删除会被拒）；
        # _given_basetemp 与 _exit_stack 是 pytest 内置实现访问的属性。
        _retention_policy = "all"
        _given_basetemp: Any = None
        _exit_stack: Any = contextlib.ExitStack()

        def getbasetemp(self) -> Path:
            return root

        def mktemp(self, basename: str, numbered: bool = True) -> Path:
            if not numbered:
                target = root / basename
                target.mkdir()
                return target
            for i in range(10000):
                candidate = root / f"{basename}{i}"
                try:
                    candidate.mkdir()
                except FileExistsError:
                    continue
                return candidate
            raise RuntimeError(f"无法在 {root} 下创建编号临时目录: {basename}")

    return SandboxSafeTempPathFactory()
