"""P06-08 Backup snapshot tool (PostgreSQL + artifacts).

备份介质（与 docs/03-DATABASE.md 第 7 节一致）：
- PostgreSQL：``pg_dump --format=custom`` 全库逻辑备份（版本化 dump 文件）；
- 工件：``artifacts_data`` 卷内容快照到 ``BACKUP_ROOT/<timestamp>/artifacts/``。

用法：
    # Docker 环境（compose 栈运行中）
    python scripts/backup_snapshot.py --docker --backup-root backup

    # 本地目录模式（离线条目复制，供单测/离线验证，不依赖 Docker）
    python scripts/backup_snapshot.py --local --source-dir artifacts --backup-root backup

输出：``BACKUP_ROOT/<timestamp>/`` 下包含：
- ``invest_db.dump``：pg_dump custom 格式（--docker 模式）
- ``artifacts/``：工件目录快照
- ``backup_manifest.json``：备份时间、路径、job_id 覆盖列表、每个文件 sha256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TS_FORMAT = "%Y%m%dT%H%M%S%fZ"
DUMP_FILENAME = "invest_db.dump"
MANIFEST_FILENAME = "backup_manifest.json"
POSTGRES_CONTAINER = "postgres"
WORKER_CONTAINER = "worker"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_job_dirs(artifacts_dir: Path) -> list[str]:
    if not artifacts_dir.is_dir():
        return []
    return sorted(p.name for p in artifacts_dir.iterdir() if p.is_dir())


def _snapshot_local_artifacts(source_dir: Path, target_dir: Path) -> dict[str, Any]:
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    checksums: dict[str, str] = {}
    for path in sorted(target_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(target_dir).as_posix()
            checksums[rel] = _sha256_file(path)
    return {"files": checksums, "job_ids": _collect_job_dirs(target_dir)}


def _run(cmd: list[str]) -> bytes:
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"命令失败 {cmd[0]} (exit={proc.returncode}): {err}")
    return proc.stdout


def _pg_dump_to_file(backup_path: Path) -> None:
    out = _run(
        [
            "docker", "compose", "exec", "-T", POSTGRES_CONTAINER,
            "pg_dump", "-Fc", "-Z", "1", "-U", "invest", "-d", "invest",
            "--no-owner",
        ]
    )
    if not out:
        raise RuntimeError("pg_dump 返回空输出，备份失败")
    backup_path.write_bytes(out)


def _docker_cp_artifacts(backup_artifacts_dir: Path) -> None:
    tmp_dir = backup_artifacts_dir.with_name(".tmp_artifacts")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    backup_artifacts_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(
            [
                "docker", "compose", "cp",
                f"{WORKER_CONTAINER}:/app/artifacts/.", f"{tmp_dir}/",
            ]
        )
        if backup_artifacts_dir.exists():
            shutil.rmtree(backup_artifacts_dir)
        tmp_dir.rename(backup_artifacts_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _snapshot_container_artifacts(backup_dir: Path) -> dict[str, Any]:
    artifacts_dir = backup_dir / "artifacts"
    _docker_cp_artifacts(artifacts_dir)
    checksums: dict[str, str] = {}
    for path in sorted(artifacts_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(artifacts_dir).as_posix()
            checksums[rel] = _sha256_file(path)
    return {"files": checksums, "job_ids": _collect_job_dirs(artifacts_dir)}


def backup_snapshot(
    *,
    backup_root: Path,
    source_dir: Path | None = None,
    use_docker: bool = False,
    timestamp: str | None = None,
) -> Path:
    ts = timestamp or _utc_now().strftime(_TS_FORMAT)
    backup_dir = backup_root / ts
    backup_dir.mkdir(parents=True, exist_ok=False)

    dump_info: dict[str, Any] | None = None
    if use_docker:
        dump_path = backup_dir / DUMP_FILENAME
        _pg_dump_to_file(dump_path)
        dump_info = {
            "filename": DUMP_FILENAME,
            "sha256": _sha256_file(dump_path),
            "byte_size": dump_path.stat().st_size,
            "format": "custom",
        }
        artifacts_info = _snapshot_container_artifacts(backup_dir)
    else:
        if source_dir is None:
            raise ValueError("local 模式必须提供 --source-dir")
        artifacts_info = _snapshot_local_artifacts(source_dir, backup_dir / "artifacts")

    manifest: dict[str, Any] = {
        "backup_time_utc": _utc_now().isoformat(),
        "timestamp": ts,
        "pg_dump": dump_info,
        "artifacts": {
            "source": "docker:worker:/app/artifacts" if use_docker else str(source_dir),
            "job_ids": artifacts_info["job_ids"],
            "files": artifacts_info["files"],
        },
    }
    manifest_path = backup_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL + artifacts 备份快照（P06-08）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--docker", action="store_true", help="Docker 模式")
    mode.add_argument("--local", action="store_true", help="本地目录模式（离线/测试）")
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, default=None)
    parser.add_argument("--timestamp", default=None)
    args = parser.parse_args()

    try:
        manifest_path = backup_snapshot(
            backup_root=args.backup_root,
            source_dir=args.source_dir,
            use_docker=args.docker,
            timestamp=args.timestamp,
        )
    except Exception as exc:  # noqa: BLE001 - 脚本边界：记录错误并返回非零退出码
        print(f"备份失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"备份完成，manifest: {manifest_path}")
    print(f"  job_ids: {manifest['artifacts']['job_ids']}")
    if manifest["pg_dump"]:
        info = manifest["pg_dump"]
        print(
            f"  pg_dump: {info['filename']} ({info['byte_size']} bytes,"
            f" sha256={info['sha256'][:12]}...)"
        )


if __name__ == "__main__":
    main()
