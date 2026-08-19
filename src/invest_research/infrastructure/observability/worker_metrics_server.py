"""P06-06C：Worker 多进程 Prometheus 指标端点。

背景：API 与 Worker 是独立容器/进程。Worker 的 prefork 子进程写入
``PROMETHEUS_MULTIPROC_DIR`` 目录下的 mmap 文件；本模块在 Celery
父进程（``worker_init`` 信号）启动一个 HTTP server，用 ``MultiProcessCollector``
聚合所有子进程的 .db 文件后提供 ``generate_latest()``。

设计决策（对齐任务要求"先审计 Celery prefork 模型和 prometheus_client 限制"）：
- prometheus_client 的多进程模式要求**在任何 prometheus_client import 之前**
  设置 ``PROMETHEUS_MULTIPROC_DIR`` 环境变量，否则 Counter/Gauge/Histogram
  仍落在进程内，子进程数据不会写文件；
- 子进程汇总：``MultiProcessCollector(CollectorRegistry())`` 在每次 scrape 时
  扫描目录下所有 pid_*.db 文件并按 label 求和；
- 父进程 HTTP server 不直接创建指标对象（只聚合），避免父进程单独写 .db；
- Worker 重启：``worker_process_shutdown`` 信号调用 ``mark_process_dead(pid)``
  删除对应 .db；容器重建时 /tmp 自动清空；
- 与 API Registry 隔离：API 进程不设置 ``PROMETHEUS_MULTIPROC_DIR``，
  使用 prometheus_client 默认单进程 REGISTRY；Prometheus 分开采集 api:8000
  与 worker:9101 两个 target，同名指标自动合并求和。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.multiprocess import MultiProcessCollector

__all__ = [
    "start_worker_metrics_server",
    "mark_worker_process_dead",
    "cleanup_multiproc_dir",
    "install_worker_signals",
]

_LOGGER = logging.getLogger(__name__)

DEFAULT_METRICS_PORT = 9101
_DEFAULT_MULTIPROC_DIR = "/tmp/prometheus_metrics"


def _multiproc_dir() -> str:
    """读取 PROMETHEUS_MULTIPROC_DIR；未配置时返回约定目录（Worker 侧统一）。"""
    return os.environ.get("PROMETHEUS_MULTIPROC_DIR", _DEFAULT_MULTIPROC_DIR)


def _registry() -> CollectorRegistry:
    """构造聚合子进程 .db 文件的 CollectorRegistry（每次调用新实例，避免重复注册）。"""
    registry = CollectorRegistry()
    MultiProcessCollector(registry, path=_multiproc_dir())  # type: ignore[no-untyped-call]
    return registry


def start_worker_metrics_server(
    port: int | None = None,
    *,
    registry_factory: Callable[[], CollectorRegistry] | None = None,
    cleaner: Callable[[], None] | None = None,
) -> None:
    """在 Celery 父进程启动一个 daemon HTTP server 暴露 Worker 聚合指标。

    - ``port``：默认 9101（与 Prometheus target ``worker:9101`` 对齐）；
    - ``registry_factory``：测试可注入 fake 聚合 registry；
    - ``cleaner``：可选启动前清理（默认清空 MULTIPROC 目录中孤儿 .db）。
    """
    resolved_port = (
        port
        if port is not None
        else int(os.environ.get("PROMETHEUS_METRICS_PORT", str(DEFAULT_METRICS_PORT)))
    )
    # 启动前清理孤儿 .db（Worker 重启后旧 pid 文件必须移除，否则指标永久残留）。
    if cleaner is not None:
        cleaner()
    resolved_factory = registry_factory if registry_factory is not None else _registry

    start_http_server(resolved_port, registry=resolved_factory())
    _LOGGER.info("worker_metrics_server_started port=%s", resolved_port)


def mark_worker_process_dead(pid: int) -> None:
    """Celery worker_process_shutdown 信号处理：删除该 pid 的 .db 文件。

    prometheus_client 的多进程模式首次导入即在文件头部写入每个子进程的
    data；正常退出时调用 ``mark_process_dead`` 让后续 scrape 不在结果中
    混入已退出进程的计数。
    """
    try:
        from prometheus_client.multiprocess import mark_process_dead

        mark_process_dead(pid, _multiproc_dir())  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001 - 清理失败不影响 worker 退出
        _LOGGER.warning("mark_process_dead_failed pid=%s", pid)


def cleanup_multiproc_dir() -> None:
    """清空 MULTIPROC 目录中的 .db 文件（启动前防止孤儿 pid 文件残留）。"""
    try:
        path = _multiproc_dir()
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            return
        for name in os.listdir(path):
            if name.endswith(".db"):
                try:
                    os.remove(os.path.join(path, name))
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 - 清理失败不影响 worker 启动
        _LOGGER.warning("cleanup_multiproc_dir_failed path=%s", path, exc_info=True)


def _setup_child_tracing() -> None:
    """P06-11J：Prefork 子进程内重新初始化 TracerProvider。

    父进程在 fork 前 setup_tracing 的 BatchSpanProcessor 导出线程不会被复制
    到子进程；每个子进程必须在 worker_process_init 内重建 provider 才有活跃
    导出线程（否则 span 永远留在内存队列不上传）。
    """
    try:
        import os

        from invest_research.infrastructure.observability.tracing import setup_tracing

        setup_tracing(
            service_name=os.environ.get("OTEL_SERVICE_NAME", "invest-research"),
            endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        )
    except Exception:  # noqa: BLE001 - 观测初始化失败不影响 worker 启动
        _LOGGER.warning("child_tracing_setup_failed")


def _flush_traces() -> None:
    """P06-11J：Worker 子进程退出前 force_flush 所有 span（尽力而为）。"""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if provider is None:
            return
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis=5000)
    except Exception:  # noqa: BLE001 - flush 失败不影响 worker 退出
        _LOGGER.warning("trace_force_flush_failed")


def install_worker_signals(app: Any) -> None:
    """把 worker_init / worker_process_shutdown 信号接到本模块（Celery 父进程）。"""
    from celery import signals  # type: ignore[import-untyped]

    def _on_worker_init(**_: object) -> None:
        start_worker_metrics_server(cleaner=cleanup_multiproc_dir)

    def _on_worker_process_init(**_: object) -> None:
        _setup_child_tracing()

    def _on_process_shutdown(pid: int, **_: object) -> None:
        mark_worker_process_dead(pid)

    def _on_worker_shutdown(**_: object) -> None:
        _flush_traces()

    signals.worker_init.connect(_on_worker_init, weak=False)
    signals.worker_process_init.connect(_on_worker_process_init, weak=False)
    signals.worker_process_shutdown.connect(_on_process_shutdown, weak=False)
    signals.worker_shutdown.connect(_on_worker_shutdown, weak=False)
