"""P06-11K-2：诊断包基础设施存储（原子写 + 目录防穿越 + 生命周期清理）。

- ``resolve_diagnostics_dir``：把 ``<artifact_root>/<job_id>/diagnostics`` 解析为
  Path。job_id 只接受 UUID 形态（防目录穿越：拒绝 ``..``、绝对路径、非 UUID）；
- ``DiagnosticsBundleStore.write``：把 ``DiagnosticBundleFiles`` 原子写盘
  （复用 ArtifactStore 的 mkstemp(dir=target.parent) + fsync + os.replace 语义），
  manifest.json 最后写（全部文件成功后）；
- ``DiagnosticsBundleStore.write_if_absent``：幂等落盘——目标表已存在（如
  manifest.json 已存在）时直接返回既有路径，不重复写（对应 finalize_failure
  重复调用的幂等语义）；
- ``DiagnosticsCleanupService``：扫描 ``<artifact_root>/*/diagnostics``，
  读取 manifest.json 的 ``expires_at``，过期则删除整个 diagnostics 目录
  （删除失败不抛，尽力而为 + 日志）。

注意：本层只写脱敏后的诊断包内容；Payload 安全规则由 application 层
``build_bundle_files`` 负责（本层不做二次业务过滤）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from invest_research.application.diagnostics.persistence import (
    BUNDLE_FILE_NAMES,
    DiagnosticBundleFiles,
)

__all__ = [
    "resolve_diagnostics_dir",
    "DiagnosticsBundleStore",
    "DiagnosticsCleanupService",
]

_LOGGER = logging.getLogger(__name__)


class InvalidDiagnosticsJobId(ValueError):
    """job_id 非法（无法映射为安全的 diagnostics 目录）。API 层可捕获转 400/404。"""


def _validate_job_id(job_id: str | uuid.UUID) -> str:
    """job_id 必须是合法 UUID 字符串（防目录穿越核心）。

    - 拒绝 ``..``、绝对路径、反斜杠、路径分隔符以外的任何字符组合；
    - 统一转为小写十六进制 UUID 形态（路径唯一且可预期）。
    """
    if isinstance(job_id, uuid.UUID):
        return str(job_id)
    try:
        parsed = uuid.UUID(str(job_id))
    except (ValueError, AttributeError) as exc:
        raise InvalidDiagnosticsJobId(f"job_id 不是合法 UUID: {job_id!r}") from exc
    return str(parsed)


def resolve_diagnostics_dir(artifact_root: str | Path, job_id: str | uuid.UUID) -> Path:
    """把 ``<artifact_root>/<job_id>/diagnostics`` 安全解析为 Path。

    ``job_id`` 必须是合法 UUID；``artifact_root`` 保持传入路径（仅用于前缀）。
    返回的目录不保证存在（调用方负责 mkdir）。
    """
    root = Path(artifact_root)
    safe_job = _validate_job_id(job_id)
    return root / safe_job / "diagnostics"


class DiagnosticsBundleStore:
    """把诊断包写到 ``artifacts/<job_id>/diagnostics``（幂等 + 原子）。"""

    def __init__(self, artifact_root: str | Path) -> None:
        self._artifact_root = Path(artifact_root)

    def write(
        self,
        job_id: str | uuid.UUID,
        files: DiagnosticBundleFiles,
    ) -> dict[str, Path]:
        """原子写 5 个文件；manifest.json 最后写（全部文件成功后）。

        - 每个文件经 ``mkstemp(dir=target.parent) + flush + fsync + os.replace``；
        - 中途失败：已写文件保留（幂等重试语义），但 manifest 未写则代表
          整个诊断包"未完成"（调用方应删除目录或重试）。
        """
        target_dir = resolve_diagnostics_dir(self._artifact_root, job_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        # 先写除 manifest 外的 4 个文件
        written: dict[str, Path] = {}
        for name in BUNDLE_FILE_NAMES:
            if name == "manifest.json":
                continue
            path = self._atomic_write(target_dir / name, files.as_dict()[name])
            written[name] = path
        # manifest 最后写
        manifest_path = self._atomic_write(
            target_dir / "manifest.json", files.manifest
        )
        written["manifest.json"] = manifest_path
        return written

    def write_if_absent(
        self,
        job_id: str | uuid.UUID,
        files: DiagnosticBundleFiles,
    ) -> dict[str, Path] | None:
        """幂等落盘：diagnostics 目录内 manifest.json 已存在则视为已落盘。

        - 已存在：返回 None（调用方视为幂等 no-op）；
        - 不存在：执行 ``write`` 并返回写入路径。
        """
        target_dir = resolve_diagnostics_dir(self._artifact_root, job_id)
        if (target_dir / "manifest.json").exists():
            return None
        return self.write(job_id, files)

    def _atomic_write(self, target: Path, content: bytes) -> Path:
        """复用 ArtifactStore 同款原子写：同目录临时文件 + fsync + os.replace。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, target)
            tmp_name = None  # type: ignore[assignment]  # 已 rename，无需清理
            return target
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass


class DiagnosticsCleanupService:
    """按 manifest.expires_at 删除过期诊断包（retention_days 由 build_bundle_files 写入）。"""

    def __init__(self, artifact_root: str | Path) -> None:
        self._artifact_root = Path(artifact_root)

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        """扫描 ``<artifact_root>/*/diagnostics`` 并删除过期目录。

        返回删除的目录数；删除失败不抛异常（记录日志，尽力而为）。
        ``now`` 供测试注入固定时间（None=当前时间）。
        """
        current = now or datetime.now(timezone.utc)
        removed = 0
        root = self._artifact_root
        if not root.exists():
            return 0
        for job_dir in root.iterdir():
            if not job_dir.is_dir():
                continue
            diag_dir = job_dir / "diagnostics"
            manifest_path = diag_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expires_raw = manifest.get("expires_at")
                if not isinstance(expires_raw, str):
                    continue
                expires_at = datetime.fromisoformat(expires_raw)
                if expires_at < current:
                    shutil.rmtree(diag_dir)
                    removed += 1
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                _LOGGER.warning("诊断包清理失败 job=%s: %s", job_dir.name, exc)
        return removed
