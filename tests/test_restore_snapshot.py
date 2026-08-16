"""P06-08: 恢复快照脚本的离线单测（不依赖 Docker）。

验证 verify_backup 对 manifest/checksum 的完整性校验逻辑。
Docker 模式（pg_restore/工件复制）作为集成演练在第 5 步真实执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backup_snapshot import backup_snapshot  # noqa: E402
from scripts.restore_snapshot import verify_backup  # noqa: E402


def _make_artifact_tree(root: Path, *job_ids: str) -> None:
    for job_id in job_ids:
        job_dir = root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "08_report.md").write_text(f"# report for {job_id}", encoding="utf-8")


def _make_backup(tmp_path: Path, timestamp: str = "20260816T150000000000Z") -> Path:
    src = tmp_path / "src"
    _make_artifact_tree(src, "job-55555555", "job-66666666")
    manifest_path = backup_snapshot(
        backup_root=tmp_path / "backups",
        source_dir=src,
        use_docker=False,
        timestamp=timestamp,
    )
    return manifest_path.parent


def test_verify_backup_ok(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)
    manifest = verify_backup(backup_dir)
    assert manifest["artifacts"]["job_ids"] == ["job-55555555", "job-66666666"]


def test_verify_backup_with_existing_job_id_ok(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)
    manifest = verify_backup(backup_dir, job_id="job-55555555")
    assert manifest["backup_time_utc"]


def test_verify_backup_job_id_missing_raises(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)
    with pytest.raises(RuntimeError, match="未覆盖"):
        verify_backup(backup_dir, job_id="job-99999999")


def test_verify_backup_tampered_artifact_raises(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)
    target = backup_dir / "artifacts" / "job-55555555" / "08_report.md"
    target.write_text("tampered content", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum 不一致"):
        verify_backup(backup_dir)


def test_verify_backup_missing_manifest_raises(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="backup_manifest"):
        verify_backup(empty_dir)
