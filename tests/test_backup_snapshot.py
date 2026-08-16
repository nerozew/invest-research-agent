"""P06-08: 备份快照脚本的离线单测（不依赖 Docker）。

验证本地模式的 manifest 生成、幂等性、checksum 一致性与 job_id 覆盖列表。
Docker 模式（pg_dump/工件复制）作为集成演练在第 5 步真实执行。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backup_snapshot import (  # noqa: E402
    MANIFEST_FILENAME,
    _collect_job_dirs,
    _sha256_file,
    backup_snapshot,
)


def _make_artifact_tree(root: Path, *job_ids: str) -> None:
    """构造与容器内结构一致的工件树（顶层目录名 = job_id）。"""
    for job_id in job_ids:
        job_dir = root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "08_report.md").write_text(f"# report for {job_id}", encoding="utf-8")


def test_local_backup_creates_manifest_and_snapshot(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_artifact_tree(src, "job-11111111", "job-22222222")

    manifest_path = backup_snapshot(
        backup_root=tmp_path / "backups",
        source_dir=src,
        use_docker=False,
        timestamp="20260816T120000000000Z",
    )

    assert manifest_path.name == MANIFEST_FILENAME
    backup_dir = manifest_path.parent
    assert (backup_dir / "artifacts" / "job-11111111" / "08_report.md").exists()
    assert (backup_dir / "artifacts" / "job-22222222" / "08_report.md").exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["timestamp"] == "20260816T120000000000Z"
    assert manifest["pg_dump"] is None  # local 模式无 pg_dump
    assert manifest["artifacts"]["job_ids"] == ["job-11111111", "job-22222222"]
    assert set(manifest["artifacts"]["files"]) == {
        "job-11111111/08_report.md",
        "job-22222222/08_report.md",
    }


def test_local_backup_checksum_matches_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_artifact_tree(src, "job-33333333")

    manifest_path = backup_snapshot(
        backup_root=tmp_path / "backups",
        source_dir=src,
        use_docker=False,
        timestamp="20260816T130000000000Z",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = "job-33333333/08_report.md"
    backup_file = manifest_path.parent / "artifacts" / rel
    assert manifest["artifacts"]["files"][rel] == _sha256_file(backup_file)
    assert manifest["artifacts"]["files"][rel] == hashlib.sha256(
        b"# report for job-33333333"
    ).hexdigest()


def test_local_backup_timestamp_conflict_raises(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_artifact_tree(src, "job-44444444")

    backup_snapshot(
        backup_root=tmp_path / "backups",
        source_dir=src,
        use_docker=False,
        timestamp="20260816T140000000000Z",
    )
    with pytest.raises(FileExistsError):
        backup_snapshot(
            backup_root=tmp_path / "backups",
            source_dir=src,
            use_docker=False,
            timestamp="20260816T140000000000Z",
        )


def test_local_backup_missing_source_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source-dir"):
        backup_snapshot(
            backup_root=tmp_path / "backups",
            source_dir=None,
            use_docker=False,
        )


def test_collect_job_dirs_sorted_and_only_dirs(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "bbb").mkdir()
    (root / "aaa").mkdir()
    (root / "plainfile.txt").write_text("not a dir", encoding="utf-8")

    assert _collect_job_dirs(root) == ["aaa", "bbb"]
