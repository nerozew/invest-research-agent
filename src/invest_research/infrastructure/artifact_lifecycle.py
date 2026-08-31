"""P06-03 本地工件生命周期清理（retention policy，确定性、可测试）。

策略（按配置 ``CleanupPolicy``）：
1. **临时文件**：ArtifactStore 原子写失败遗留的 ``.tmp-*.part`` 孤儿文件，
   超过 ``temp_file_max_age_seconds`` 未修改才清理（新鲜临时文件可能属于
   进行中的写入，不删）；
2. **过期任务工件**：``artifact_root`` 下的任务目录（如 ``AAPL_2025-10-31``）
   超过 ``job_retention_days`` 未修改则整体删除；目录含 ``.keep`` 标记文件
   则跳过（人工保留）；
3. **基准工件**：``benchmark_root`` 下的运行目录（``<run_id>/``）只保留最近
   ``benchmark_keep_runs`` 个（按目录 mtime 排序，新的在前），其余删除。

安全与边界：
- 默认 ``dry_run=True``：只统计不删除；
- 永不删除 ``artifact_root`` / ``benchmark_root`` 本身；
- 删除只发生在调用方显式给出的根目录内（测试一律使用临时目录）；
- 根目录不存在时静默返回零统计；根路径是文件时抛 ValueError。

对齐 docs/05 P06-03 与 docs/04 §5.3：失败工件可清理或保留为可识别临时文件。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ArtifactStore 的临时文件命名（见 tools/artifact_store.py：prefix=".tmp-" suffix=".part"）
_TEMP_PATTERN = ".tmp-*.part"


@dataclass(frozen=True)
class CleanupPolicy:
    """清理策略（全部字段有默认值；dry_run 由服务构造参数控制）。"""

    temp_file_max_age_seconds: float = 3600.0
    job_retention_days: int = 30
    benchmark_keep_runs: int = 5
    keep_marker: str = ".keep"


@dataclass(frozen=True)
class CleanupStats:
    """一次清理的统计结果（计数 + 释放字节数）。"""

    temp_files_removed: int = 0
    expired_jobs_removed: int = 0
    benchmark_runs_removed: int = 0
    bytes_freed: int = 0

    @property
    def total_removed(self) -> int:
        return self.temp_files_removed + self.expired_jobs_removed + self.benchmark_runs_removed


def _dir_mtime(path: Path) -> datetime:
    """目录最近修改时间（UTC，时区感知）。"""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _dir_bytes(path: Path) -> int:
    """目录内文件总字节数。"""
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


def _delete(path: Path, dry_run: bool) -> None:
    """按 dry_run 删除文件/目录；目录用 shutil.rmtree。"""
    if dry_run:
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


class ArtifactCleanupService:
    """工件清理服务：临时文件 / 过期任务 / 基准运行按策略处理。

    - ``artifact_root``：任务工件根目录（必填）；
    - ``benchmark_root``：基准运行根目录（可选，不提供则跳过基准清理）；
    - ``now``：用于测试注入的"当前时间"（默认取真实时间）；
    - ``dry_run``：默认 True（只统计不删除）。
    """

    def __init__(
        self,
        artifact_root: Path,
        *,
        benchmark_root: Path | None = None,
        policy: CleanupPolicy | None = None,
        now: datetime | None = None,
        dry_run: bool = True,
    ) -> None:
        if artifact_root.exists() and not artifact_root.is_dir():
            raise ValueError(f"artifact_root 不是目录: {artifact_root}")
        if benchmark_root is not None and benchmark_root.exists() and not benchmark_root.is_dir():
            raise ValueError(f"benchmark_root 不是目录: {benchmark_root}")
        self._artifact_root = artifact_root
        self._benchmark_root = benchmark_root
        self._policy = policy if policy is not None else CleanupPolicy()
        self._now = now if now is not None else datetime.now(timezone.utc)
        self._dry_run = dry_run
        self._freed_bytes = 0

    def cleanup(self) -> CleanupStats:
        """执行一次清理，返回统计（dry_run 时只统计不删除）。"""
        self._freed_bytes = 0
        return CleanupStats(
            temp_files_removed=self._cleanup_temp_files(),
            expired_jobs_removed=self._cleanup_expired_jobs(),
            benchmark_runs_removed=self._cleanup_benchmark_runs(),
            bytes_freed=self._freed_bytes,
        )

    # ---- 实现 ----

    def _cleanup_temp_files(self) -> int:
        """清理过期的 .tmp-*.part 孤儿临时文件，返回删除个数。"""
        if not self._artifact_root.exists():
            return 0
        removed = 0
        cutoff = self._now - timedelta(seconds=self._policy.temp_file_max_age_seconds)
        for path in self._artifact_root.rglob(_TEMP_PATTERN):
            if not path.is_file():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime > cutoff:
                continue  # 新鲜临时文件：可能属于进行中的写入
            self._freed_bytes += path.stat().st_size
            _delete(path, self._dry_run)
            removed += 1
        return removed

    def _cleanup_expired_jobs(self) -> int:
        """清理超过保留期的任务工件目录（含 .keep 标记的跳过）。"""
        if not self._artifact_root.exists():
            return 0
        removed = 0
        cutoff = self._now - timedelta(days=self._policy.job_retention_days)
        for child in sorted(self._artifact_root.iterdir()):
            if not child.is_dir():
                continue
            if (child / self._policy.keep_marker).exists():
                continue  # 人工保留标记
            if _dir_mtime(child) > cutoff:
                continue  # 未过期
            self._freed_bytes += _dir_bytes(child)
            _delete(child, self._dry_run)
            removed += 1
        return removed

    def _cleanup_benchmark_runs(self) -> int:
        """只保留最近 benchmark_keep_runs 个基准运行目录，其余删除。"""
        if self._benchmark_root is None or not self._benchmark_root.exists():
            return 0
        runs = sorted(
            (c for c in self._benchmark_root.iterdir() if c.is_dir()),
            key=_dir_mtime,
            reverse=True,  # 新的在前
        )
        removed = 0
        for run_dir in runs[self._policy.benchmark_keep_runs :]:
            self._freed_bytes += _dir_bytes(run_dir)
            _delete(run_dir, self._dry_run)
            removed += 1
        return removed
