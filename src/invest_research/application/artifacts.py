"""工件清单与安全下载的用例与端口（P04-04）。

安全模型：
- ``artifact_key`` 校验规则与 ArtifactStore（P02-12）一致（防路径穿越）：非空、无首尾空白、
  无反斜杠、无危险字符、非绝对路径、无 ``..`` 段。
- 端口只暴露"按 job 读登记 / 按 key 读内容"，不暴露磁盘路径；读取不存在或不属于
  该 job 的 key 返回 None（由 API 层转 404）。
- 下载路径由 infrastructure 实现负责（用 ArtifactStore._resolve + 对照 artifacts 表登记），
  本层保持零文件系统/DB 依赖。
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

# key 中禁止出现的字符（与 tools/artifact_store.py 对齐，防止路径穿越与危险文件名）
_FORBIDDEN_KEY_CHARS = frozenset("\x00 \t\r\n;<>[]|?*")


class InvalidArtifactKey(ValueError):
    """artifact_key 非法（路径穿越/危险字符）。API 层捕获后转 400。"""


class ArtifactInfo(BaseModel):
    """工件清单条目（沿用 artifacts 表元数据字段名）。"""

    model_config = ConfigDict(frozen=True)

    artifact_key: str
    artifact_type: str
    schema_version: str | None = None
    storage_uri: str
    content_checksum: str
    byte_size: int = Field(ge=0)


class ArtifactCatalogStore(Protocol):
    """工件登记端口：返回某 job 已登记的工件清单。"""

    def list_artifacts(self, job_id: uuid.UUID) -> tuple[ArtifactInfo, ...]: ...


class ArtifactContentStore(Protocol):
    """工件内容端口：按 job+key 读取字节；不存在或不属于该 job 返回 None。"""

    def read(self, job_id: uuid.UUID, artifact_key: str) -> bytes | None: ...


def _validate_artifact_key(key: str) -> None:
    """校验工件 key 安全性；非法抛 InvalidArtifactKey（API 层转 400）。"""
    if not key or not key.strip():
        raise InvalidArtifactKey("artifact_key 不能为空")
    if key != key.strip():
        raise InvalidArtifactKey("artifact_key 首尾不能有空白")
    if "\\" in key:
        raise InvalidArtifactKey("artifact_key 不能包含反斜杠")
    if any(c in _FORBIDDEN_KEY_CHARS for c in key):
        raise InvalidArtifactKey(f"artifact_key 含非法字符: {key}")
    parts = PurePosixPath(key).parts
    if PurePosixPath(key).is_absolute():
        raise InvalidArtifactKey(f"artifact_key 不能是绝对路径: {key}")
    if ".." in parts:
        raise InvalidArtifactKey(f"artifact_key 不能包含 .. 段: {key}")


class GetJobArtifactsService:
    """列出某 job 已登记工件（FR-009 中间产出可见性）。"""

    def __init__(self, catalog: ArtifactCatalogStore) -> None:
        self._catalog = catalog

    def list(self, job_id: uuid.UUID) -> tuple[ArtifactInfo, ...]:
        return self._catalog.list_artifacts(job_id)


class GetJobArtifactContentService:
    """安全读取某 job 的工件内容（仅允许已登记工件 + 路径穿越防护）。"""

    def __init__(self, content_store: ArtifactContentStore) -> None:
        self._content_store = content_store

    def read(self, job_id: uuid.UUID, artifact_key: str) -> bytes | None:
        _validate_artifact_key(artifact_key)
        return self._content_store.read(job_id, artifact_key)
