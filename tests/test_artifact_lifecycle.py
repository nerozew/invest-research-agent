"""P06-03 工件生命周期清理测试（retention policy）。

安全约束：**所有删除测试只使用 pytest 临时目录**，绝不触碰真实 artifacts/。

验证目标（docs/05 P06-03）：
- 临时文件（.tmp-*.part 孤儿）按年龄清理，新鲜临时文件保留；
- 过期任务工件目录按保留天数清理，.keep 标记保护；
- 基准运行目录只保留最近 N 个；
- dry_run 只统计不删除；删除模式真实删除并统计字节；
- 根目录缺失为 no-op；根路径是文件时报错。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from invest_research.infrastructure.artifact_lifecycle import (
    ArtifactCleanupService,
    CleanupPolicy,
    CleanupStats,
)

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _set_mtime(path: Path, age: timedelta) -> None:
    """把文件/目录的 mtime 设为 NOW - age（用于年龄控制）。"""
    ts = (NOW - age).timestamp()
    os.utime(path, (ts, ts))


def _touch(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _service(
    artifact_root: Path,
    *,
    benchmark_root: Path | None = None,
    policy: CleanupPolicy | None = None,
    dry_run: bool = True,
) -> ArtifactCleanupService:
    return ArtifactCleanupService(
        artifact_root,
        benchmark_root=benchmark_root,
        policy=policy,
        now=NOW,
        dry_run=dry_run,
    )


# ---- 1. 临时文件 ----

def test_old_temp_files_removed_fresh_kept(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    old = root / "AAPL_2025-10-31" / ".tmp-abc123.part"
    fresh = root / "MSFT_2025-12-31" / ".tmp-def456.part"
    _touch(old)
    _touch(fresh)
    _set_mtime(old, timedelta(hours=2))  # 超过 1 小时阈值
    _set_mtime(fresh, timedelta(seconds=10))  # 新鲜：保留

    stats = _service(root, dry_run=False).cleanup()
    assert stats.temp_files_removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_temp_files_do_not_touch_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    report = root / "AAPL_2025-10-31" / "08_report.md"
    _touch(report)
    _set_mtime(report, timedelta(days=90))

    stats = _service(root, dry_run=False).cleanup()
    assert stats.temp_files_removed == 0
    assert report.exists()


def test_temp_file_age_boundary(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    at_cutoff = root / ".tmp-boundary.part"
    _touch(at_cutoff)
    _set_mtime(at_cutoff, timedelta(seconds=3600))  # 恰好等于阈值：视为过期

    stats = _service(root, dry_run=False).cleanup()
    assert stats.temp_files_removed == 1
    assert not at_cutoff.exists()


# ---- 2. 过期任务工件 ----

def test_expired_job_dir_removed_recent_kept(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    old_job = root / "AAPL_2025-10-31"
    new_job = root / "MSFT_2025-12-31"
    _touch(old_job / "07_manifest.json")
    _touch(new_job / "07_manifest.json")
    _set_mtime(old_job, timedelta(days=40))  # 超过 30 天
    _set_mtime(new_job, timedelta(days=1))  # 未过期

    stats = _service(root, dry_run=False).cleanup()
    assert stats.expired_jobs_removed == 1
    assert not old_job.exists()
    assert new_job.exists()


def test_keep_marker_protects_job_dir(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    kept = root / "KEEP_2024-01-01"
    _touch(kept / "07_manifest.json")
    _touch(kept / ".keep")
    _set_mtime(kept, timedelta(days=400))

    stats = _service(root, dry_run=False).cleanup()
    assert stats.expired_jobs_removed == 0
    assert kept.exists()


def test_job_retention_boundary(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    at_cutoff = root / "BOUNDARY_2025-12-16"
    _touch(at_cutoff / "07_manifest.json")
    _set_mtime(at_cutoff, timedelta(days=30))  # 恰好等于保留期：视为过期

    stats = _service(root, dry_run=False).cleanup()
    assert stats.expired_jobs_removed == 1
    assert not at_cutoff.exists()


# ---- 3. 基准工件 ----

def test_benchmark_keeps_last_n_runs(tmp_path: Path) -> None:
    benchmark_root = tmp_path / "runs"
    for i in range(8):
        run = benchmark_root / f"run{i:02d}"
        _touch(run / "case_001.json")
        _set_mtime(run, timedelta(days=8 - i))  # run00 最老 … run07 最新

    stats = _service(
        tmp_path / "artifacts",
        benchmark_root=benchmark_root,
        policy=CleanupPolicy(benchmark_keep_runs=5),
        dry_run=False,
    ).cleanup()
    assert stats.benchmark_runs_removed == 3
    remaining = sorted(p.name for p in benchmark_root.iterdir())
    assert remaining == ["run03", "run04", "run05", "run06", "run07"]


def test_benchmark_root_none_skips(tmp_path: Path) -> None:
    stats = _service(tmp_path / "artifacts", dry_run=False).cleanup()
    assert stats.benchmark_runs_removed == 0


# ---- 4. dry_run 与删除模式 ----

def test_dry_run_counts_without_deleting(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    old_job = root / "AAPL_2025-10-31"
    old_tmp = root / ".tmp-zzz.part"
    _touch(old_job / "07_manifest.json")
    _touch(old_tmp)
    _set_mtime(old_job, timedelta(days=90))
    _set_mtime(old_tmp, timedelta(days=90))

    stats = _service(root, dry_run=True).cleanup()  # 默认 dry_run
    assert stats.expired_jobs_removed == 1
    assert stats.temp_files_removed == 1
    assert old_job.exists()  # 未删除
    assert old_tmp.exists()


def test_delete_mode_removes_and_counts_bytes(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    job = root / "AAPL_2025-10-31"
    _touch(job / "07_manifest.json", b"12345")  # 5 字节
    _touch(root / ".tmp-aaa.part", b"12")  # 2 字节
    _set_mtime(job, timedelta(days=60))
    _set_mtime(root / ".tmp-aaa.part", timedelta(hours=5))

    stats = _service(root, dry_run=False).cleanup()
    assert isinstance(stats, CleanupStats)
    assert stats.total_removed == 2
    assert stats.bytes_freed == 7
    assert not job.exists()
    assert not (root / ".tmp-aaa.part").exists()


# ---- 5. 边界与防御 ----

def test_missing_roots_are_noop(tmp_path: Path) -> None:
    stats = _service(
        tmp_path / "no-such-artifacts",
        benchmark_root=tmp_path / "no-such-runs",
        dry_run=False,
    ).cleanup()
    assert stats.total_removed == 0


def test_root_path_is_file_raises(tmp_path: Path) -> None:
    file_root = tmp_path / "not_a_dir"
    file_root.write_text("x", encoding="utf-8")
    import pytest

    with pytest.raises(ValueError):
        _service(file_root)


def test_never_deletes_roots_themselves(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    benchmark_root = tmp_path / "runs"
    old_job = root / "AAPL_2025-10-31"
    old_run = benchmark_root / "run00"
    _touch(old_job / "f.json")
    _touch(old_run / "c.json")
    _set_mtime(old_job, timedelta(days=99))
    _set_mtime(old_run, timedelta(days=99))

    _service(
        root,
        benchmark_root=benchmark_root,
        policy=CleanupPolicy(benchmark_keep_runs=1),
        dry_run=False,
    ).cleanup()
    assert root.exists()
    assert benchmark_root.exists()
