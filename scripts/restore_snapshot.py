"""P06-08 Restore snapshot tool (PostgreSQL + artifacts).

恢复介质（与 docs/03-DATABASE.md 第 7 节一致）：
- PostgreSQL：``pg_restore`` 从 custom dump 恢复（--clean --if-exists 全库重建）；
- 工件：将快照的 ``artifacts/<job_id>/`` 复制回 worker 容器 ``/app/artifacts/<job_id>/``。

用法：
    # 校验快照完整性（不写入，只读）
    python scripts/restore_snapshot.py --verify <backup_dir>

    # 校验快照且确认包含某 job_id（不写入）
    python scripts/restore_snapshot.py --verify <backup_dir> --job-id 43ce04f2-...

    # 全库恢复 PostgreSQL（灾难恢复；会先 DROP 并重建所有对象）
    python scripts/restore_snapshot.py --restore-db <backup_dir>

    # 恢复单个 job 的工件到 worker 容器
    python scripts/restore_snapshot.py --restore-artifacts <backup_dir> --job-id 43ce04f2-...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DUMP_FILENAME = "invest_db.dump"
MANIFEST_FILENAME = "backup_manifest.json"
POSTGRES_CONTAINER = "postgres"
WORKER_CONTAINER = "worker"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(cmd: list[str], input_data: bytes | None = None) -> bytes:
    proc = subprocess.run(cmd, input=input_data, capture_output=True, timeout=300)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"命令失败 {cmd[0]} (exit={proc.returncode}): {err}")
    return proc.stdout


def load_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"备份目录缺少 {MANIFEST_FILENAME}: {backup_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_backup(backup_dir: Path, job_id: str | None = None) -> dict[str, Any]:
    """校验备份快照完整性：manifest 存在、pg_dump/工件的 checksum 一致。"""
    manifest = load_manifest(backup_dir)

    pg_dump_info = manifest.get("pg_dump")
    if pg_dump_info:
        dump_path = backup_dir / pg_dump_info["filename"]
        if not dump_path.exists():
            raise RuntimeError(f"pg_dump 文件缺失: {dump_path}")
        actual = _sha256_file(dump_path)
        if actual != pg_dump_info["sha256"]:
            raise RuntimeError(
                f"pg_dump checksum 不一致: 期望 {pg_dump_info['sha256']}, 实际 {actual}"
            )

    for rel, expected in manifest["artifacts"]["files"].items():
        fpath = backup_dir / "artifacts" / rel
        if not fpath.exists():
            raise RuntimeError(f"工件文件缺失: {fpath}")
        actual = _sha256_file(fpath)
        if actual != expected:
            raise RuntimeError(
                f"工件 checksum 不一致 ({rel}): 期望 {expected}, 实际 {actual}"
            )

    if job_id is not None:
        if job_id not in manifest["artifacts"]["job_ids"]:
            raise RuntimeError(
                f"备份未覆盖 job_id {job_id}；"
                f"备份包含: {manifest['artifacts']['job_ids']}"
            )
    return manifest


def _pg_restore_db(backup_dir: Path) -> None:
    verify_backup(backup_dir)
    dump_path = backup_dir / DUMP_FILENAME
    if not dump_path.exists():
        raise RuntimeError(f"备份目录缺少 dump 文件: {dump_path}")

    _run(
        [
            "docker", "compose", "exec", "-T", POSTGRES_CONTAINER,
            "pg_restore", "--clean", "--if-exists", "--no-owner", "--exit-on-error",
            "--dbname", "invest", "-U", "invest",
        ],
        input_data=dump_path.read_bytes(),
    )


def _restore_artifacts(backup_dir: Path, job_id: str) -> None:
    verify_backup(backup_dir, job_id=job_id)
    src_dir = backup_dir / "artifacts" / job_id
    if not src_dir.is_dir():
        raise RuntimeError(f"备份中不存在任务 {job_id} 的工件目录: {src_dir}")

    _run(
        [
            "docker", "compose", "exec", "-T", WORKER_CONTAINER,
            "sh", "-c", f"rm -rf /app/artifacts/{job_id}",
        ]
    )
    _run(
        [
            "docker", "compose", "cp",
            f"{src_dir}", f"{WORKER_CONTAINER}:/app/artifacts/{job_id}",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL + artifacts 恢复（P06-08）")
    parser.add_argument(
        "backup_dir", type=Path, help="备份快照目录（含 backup_manifest.json）"
    )
    parser.add_argument("--job-id", default=None, help="目标 job_id（校验/工件恢复）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", action="store_true", help="只读校验快照完整性")
    mode.add_argument(
        "--restore-db",
        action="store_true",
        help="全库恢复 PostgreSQL（危险：先 DROP）",
    )
    mode.add_argument(
        "--restore-artifacts",
        action="store_true",
        help="恢复指定 job 的工件到容器",
    )
    args = parser.parse_args()

    try:
        if args.verify:
            manifest = verify_backup(args.backup_dir, job_id=args.job_id)
            print(f"校验通过: {args.backup_dir}")
            print(f"  job_ids: {manifest['artifacts']['job_ids']}")
            if manifest["pg_dump"]:
                info = manifest["pg_dump"]
                print(
                    f"  pg_dump: {info['filename']} ({info['byte_size']} bytes,"
                    f" sha256={info['sha256'][:12]}...)"
                )
        elif args.restore_db:
            _pg_restore_db(args.backup_dir)
            print("PostgreSQL 全库恢复完成")
        elif args.restore_artifacts:
            if args.job_id is None:
                raise ValueError("--restore-artifacts 必须提供 --job-id")
            _restore_artifacts(args.backup_dir, args.job_id)
            print(f"工件恢复完成: {args.job_id}")
    except Exception as exc:  # noqa: BLE001 - 脚本边界：记录错误并返回非零退出码
        print(f"恢复失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
